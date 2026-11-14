"""
semantic_datasource — demo data that reads like a business, not like a fixture.

Same contract as :class:`SyntheticDataSource` (pure, deterministic, FK-aware,
``generate(model) -> rows per table``); a different answer to *what value goes
in this cell*.  The uniform generator answers "a value of the right TYPE"; this
one answers "a value of the right MEANING", read from the signal the model
authors already declared and from the column's own name:

    1. FK              → a parent key, Zipf-skewed so a few customers / products
                         are "hot" (what makes rankings and top-N interesting)
    2. value_regex     → the declared closed list (weighted) or the declared shape
                         (via regex_gen), with real ISO vocabularies where the
                         description names the standard
    3. description     → examples the author wrote ("e.g., T24, FIS_PROFILE")
    4. column name     → names, places, e-mails, phones, amounts, rates, counts,
                         scores, dates, flags, codes, free text
    5. type            → the uniform fallback (the old generator's rule)

Three properties make the rows COHERENT rather than merely plausible:

    * a ROW CONTEXT — one person, one place, one anchor date per row — so the
      city, state, country, postal code, phone prefix and currency agree, and
      the e-mail is derived from the name beside it;
    * TIME flows through foreign keys — a child row's anchor is at or after its
      parents' (an order is placed after the customer signed up), and within a
      row start <= end, created <= updated, birth is 18–90 years before;
    * ROLE-SCALED volume — event tables (transactions, orders) get several
      times the rows of master tables, reference tables stay small — decided
      from the FK graph (see ``_infer_roles``), under a per-model cap.

Every column is compiled ONCE into a generator closure (``_plan_column``), so a
200k-row model costs seconds, and the decision per column is inspectable
(``describe_plan``) — the generator can explain why a cell holds what it holds.

Determinism: same (model, base rows, cap, seed) → byte-identical output.
"""

from __future__ import annotations

import bisect
import math
import random
import re
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import regex_gen, vocab
from .datasource import SyntheticDataSource, TableKey
from .physical_model import PhysColumn, PhysTable, PhysicalModel

__all__ = ["SemanticDataSource", "ROLE_ROW_FACTORS", "DEFAULT_MAX_ROWS_PER_MODEL"]

# ── volume policy ─────────────────────────────────────────────────────────────
# Base = rows_per_table; a table's target = base × factor(role), then the whole
# model is scaled down proportionally to the cap (never below MIN_ROWS).
ROLE_ROW_FACTORS: Dict[str, float] = {
    "event": 5.0, "hub": 2.0, "master": 1.0, "other": 1.0, "reference": 0.4,
}
DEFAULT_MAX_ROWS_PER_MODEL = 200_000
MIN_ROWS = 5
LOOKUP_MAX_ROWS = 40

# ── time policy ───────────────────────────────────────────────────────────────
HORIZON_DAYS = 3 * 365          # anchors fall in the three years up to TODAY
TODAY = date(2026, 9, 18)       # fixed: determinism must not depend on the clock
_RECENCY_EXP = 1.6              # days_ago = HORIZON × u^exp → skewed toward recent

_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokens(name: str) -> List[str]:
    return [t for t in re.split(r"[^a-z0-9]+", _CAMEL_RE.sub("_", name or "").lower()) if t]


def _has(toks: Sequence[str], *words: str) -> bool:
    s = set(toks)
    return any(w in s for w in words)


def _endswith(toks: Sequence[str], *words: str) -> bool:
    return bool(toks) and toks[-1] in words


_ASCII_MAP = str.maketrans({"ı": "i", "ß": "ss", "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe",
                            "Œ": "OE", "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "\u2013": "-", "\u2014": "-",
                            "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u00a0": " "})


def _ascii(text: str) -> str:
    """Transliterate to plain ASCII (the deployed columns are CHARACTER SET LATIN)."""
    if text.isascii():
        return text
    t = unicodedata.normalize("NFKD", text.translate(_ASCII_MAP))
    return t.encode("ascii", "ignore").decode("ascii")


def _decimal_spec(dbx_type: str) -> Tuple[int, int]:
    m = re.search(r"\(\s*(\d+)\s*,\s*(\d+)\s*\)", dbx_type or "")
    return (int(m.group(1)), int(m.group(2))) if m else (18, 2)


def _fit_decimal(value: float, p: int, s: int) -> Decimal:
    """Round to scale and clamp to the column's precision (never overflow)."""
    limit = 10 ** max(1, p - s) - 1
    v = max(0.0, min(float(value), float(limit)))
    q = Decimal(1).scaleb(-s) if s > 0 else Decimal(1)
    return Decimal(str(v)).quantize(q)


# ── row context ───────────────────────────────────────────────────────────────

class _RowCtx:
    __slots__ = ("i", "anchor", "first", "last", "place", "company")

    def __init__(self, i: int, anchor: date, rng: random.Random, sequential_place: bool = False):
        self.i = i
        self.anchor = anchor
        self.first = rng.choice(vocab.FIRST_NAMES)
        self.last = rng.choice(vocab.LAST_NAMES)
        if sequential_place:
            # a reference table (countries, currencies …) wants DISTINCT rows
            self.place = vocab.COUNTRY_PLACES[i % len(vocab.COUNTRY_PLACES)]
        else:
            # weighted toward the head of PLACES (home-market skew, see vocab)
            idx = int(len(vocab.PLACES) * (rng.random() ** vocab.PLACE_WEIGHT_EXPONENT))
            self.place = vocab.PLACES[min(idx, len(vocab.PLACES) - 1)]
        self.company = f"{rng.choice(vocab.COMPANY_STEMS)} {rng.choice(vocab.COMPANY_SUFFIXES)}"


Gen = Callable[[random.Random, _RowCtx], Any]


# ── the generator ─────────────────────────────────────────────────────────────

class SemanticDataSource(SyntheticDataSource):
    """Name-, regex- and role-aware demo rows.  See the module docstring."""

    def __init__(self, rows_per_table: int = 20, seed: int = 1469,
                 max_rows_per_model: int = DEFAULT_MAX_ROWS_PER_MODEL,
                 null_rate: float = 0.03):
        super().__init__(rows_per_table=rows_per_table, seed=seed)
        self.max_rows_per_model = max(MIN_ROWS, int(max_rows_per_model or DEFAULT_MAX_ROWS_PER_MODEL))
        self.null_rate = max(0.0, min(0.5, float(null_rate)))
        self._plan_notes: Dict[Tuple[str, str, str], str] = {}
        self._roles: Dict[TableKey, str] = {}
        self._rows_planned: Dict[TableKey, int] = {}

    # ── public: volume + explanation ─────────────────────────────────────────

    def plan_rows(self, model: PhysicalModel) -> Dict[TableKey, int]:
        """Rows per table BEFORE generation: role factor × base, capped per model."""
        roles = self._infer_roles(model)
        planned: Dict[TableKey, int] = {}
        for t in model.tables:
            key = (t.database_name, t.name)
            role = roles.get(key, "unknown")
            n = int(round(self.rows_per_table * ROLE_ROW_FACTORS.get(role, 1.0)))
            if role == "reference":
                n = min(n, LOOKUP_MAX_ROWS)
            planned[key] = max(MIN_ROWS, n)
        total = sum(planned.values())
        if total > self.max_rows_per_model:
            f = self.max_rows_per_model / float(total)
            planned = {k: max(MIN_ROWS, int(v * f)) for k, v in planned.items()}
        self._roles = roles
        self._rows_planned = planned
        return planned

    def describe_plan(self) -> Dict[Tuple[str, str, str], str]:
        """(database, table, column) → one line saying how the cell is filled."""
        return dict(self._plan_notes)

    def roles(self) -> Dict[TableKey, str]:
        return dict(self._roles)

    # ── generation ───────────────────────────────────────────────────────────

    def generate(self, model: PhysicalModel) -> Dict[TableKey, List[Dict[str, Any]]]:
        fk_map = self.fk_map_for(model)
        planned = self.plan_rows(model)
        order = self._topo_order(model)
        generated: Dict[TableKey, List[Dict[str, Any]]] = {}
        # per table: (anchors sorted ascending, row indices in that order) — what lets
        # a child pick a parent that already EXISTED at the child's own date
        timelines: Dict[TableKey, Tuple[List[date], List[int]]] = {}
        self._plan_notes.clear()

        for table in order:
            key = (table.database_name, table.name)
            rng = random.Random(f"{self.seed}:semantic:{key[0]}.{key[1]}")
            rows, table_anchors = self._gen_table_semantic(
                table, rng, fk_map, generated, timelines, planned.get(key, self.rows_per_table),
                self._roles.get(key, "other"))
            generated[key] = rows
            order_idx = sorted(range(len(table_anchors)), key=lambda j: table_anchors[j])
            timelines[key] = ([table_anchors[j] for j in order_idx], order_idx)
        return generated

    # ── volume classes ───────────────────────────────────────────────────────
    # Row VOLUME is decided by where a table sits in the FK graph, not by the
    # catalog's fact/dimension verdict: in these models every table carries five
    # or more ``*_id`` foreign keys and a few DECIMALs, so the warehouse rule reads
    # nearly all of them as facts.  The graph is unambiguous about volume:
    #   reference — referenced by others, references nothing (countries, statuses)
    #   master    — referenced, few outgoing keys (customers, products, accounts)
    #   hub       — referenced AND references many (accounts linking parties/products)
    #   event     — a leaf that references others (transactions, orders, events)
    # Name hints reuse the catalog's own vocabulary so there is ONE list of words.

    def _infer_roles(self, model: PhysicalModel) -> Dict[TableKey, str]:
        try:
            from trusted_data_agent.dbcatalog.intelligence import _ROLE_NAME_HINTS as hints
        except Exception:  # noqa: BLE001
            hints = {"lookup": ("ref", "lookup", "code", "type", "status", "category"),
                     "fact": ("transaction", "event", "order", "payment", "activity"),
                     "log": ("log", "audit", "history", "snapshot")}
        resolve = self.parent_db_resolver(model)
        out_deg: Dict[TableKey, set] = {}
        in_deg: Dict[TableKey, set] = {}
        for fk in model.fks:
            sdb = resolve(fk.src_domain, fk.src_table); pdb = resolve(fk.tgt_domain, fk.tgt_table)
            if not sdb or not pdb:
                continue
            skey, pkey = (sdb, fk.src_table), (pdb, fk.tgt_table)
            if skey == pkey:
                continue
            out_deg.setdefault(skey, set()).add(pkey)
            in_deg.setdefault(pkey, set()).add(skey)
        roles: Dict[TableKey, str] = {}
        for t in model.tables:
            key = (t.database_name, t.name)
            toks = set(_tokens(t.logical or t.name))
            dtoks = set(_tokens(t.domain))
            n_out, n_in = len(out_deg.get(key, ())), len(in_deg.get(key, ()))
            if dtoks & {"reference", "ref", "lookup", "master"} or (n_in >= 4 and n_out <= 2):
                role = "reference"          # the model's own reference domain / widely shared
            elif toks & set(hints.get("lookup", ())) and n_out <= 1:
                role = "reference"
            elif toks & (set(hints.get("fact", ())) | set(hints.get("log", ()))):
                role = "event"
            elif n_in >= 1 and n_out == 0:
                role = "reference"
            elif n_in == 0 and n_out >= 1:
                role = "event"
            elif n_in >= 1 and n_out >= 3:
                role = "hub"
            elif n_in >= 1:
                role = "master"
            else:
                role = "other"
            roles[key] = role
        return roles

    # ── per-table generation ─────────────────────────────────────────────────

    def _gen_table_semantic(self, table: PhysTable, rng: random.Random, fk_map, generated,
                            timelines, n_rows: int, role: str = "other") -> Tuple[List[Dict[str, Any]], List[date]]:
        pk_set = set(table.pk_columns)
        plans: List[Tuple[PhysColumn, str, Any]] = []   # (column, kind, payload)
        for col in table.columns:
            fk_target = fk_map.get((table.domain, table.name, col.name))
            if fk_target:
                pkey = (fk_target[0], fk_target[1])
                plans.append((col, "fk", (pkey, fk_target[2])))
                self._note(table, col, f"foreign key → {fk_target[1]}.{fk_target[2]} (Zipf-skewed parent)")
            elif col.name in pk_set:
                plans.append((col, "pk", self._pk_style(table, col)))
                self._note(table, col, "primary key (sequential)")
            else:
                plans.append((col, "gen", self._plan_column(table, col)))

        # Date-order constraints inside a row: (earlier, later) column pairs.
        order_pairs = self._order_pairs(table)

        rows: List[Dict[str, Any]] = []
        table_anchors: List[date] = []
        seen_pk: set = set()
        self_key = (table.database_name, table.name)
        for i in range(n_rows):
            # 1. the row's own moment in time (trend-weighted toward recent) …
            anchor = self._draw_anchor(rng)
            # 2. … then its parents, chosen among those that already existed by then
            #    (a transaction belongs to an account opened BEFORE it); a child older
            #    than every parent is lifted to the earliest parent instead.
            fk_values: Dict[str, Any] = {}
            for col, kind, payload in plans:
                if kind != "fk":
                    continue
                pkey, pcol = payload
                if pkey == self_key:                                # self-reference: rows so far
                    val = self._sample_uniform(rows, pcol, rng)
                else:
                    prow_list = generated.get(pkey) or []
                    tl = timelines.get(pkey)
                    parent_role = self._roles.get(pkey, "other")
                    val, lifted = self._sample_parent(prow_list, tl, pcol, rng, anchor,
                                                      timed=(parent_role != "reference"))
                    if lifted is not None and lifted > anchor:
                        anchor = lifted
                if val is not None and col.nullable and rng.random() < 0.02:
                    val = None                                     # a few unassigned children
                fk_values[col.name] = val
            ctx = _RowCtx(i, anchor, rng, sequential_place=(role == "reference"))

            # 2. the rest of the row
            row: Dict[str, Any] = {}
            for col, kind, payload in plans:
                if kind == "fk":
                    row[col.name] = fk_values[col.name]
                elif kind == "pk":
                    row[col.name] = payload(i)
                else:
                    v = payload(rng, ctx)
                    row[col.name] = _ascii(v) if isinstance(v, str) else v
            for earlier, later in order_pairs:
                a, b = row.get(earlier), row.get(later)
                if a is not None and b is not None and _precedes(b, a):
                    row[later] = _shift_like(b, a, rng)
            if table.pk_columns:
                row = self._ensure_unique_pk(row, table.pk_columns, seen_pk, i)
            rows.append(row)
            table_anchors.append(anchor)
        return rows, table_anchors

    def _note(self, table: PhysTable, col: PhysColumn, text: str) -> None:
        self._plan_notes[(table.database_name, table.name, col.name)] = text

    # ── parents, anchors ─────────────────────────────────────────────────────

    @staticmethod
    def _sample_parent(parent_rows, timeline, pcol, rng, child_anchor: date,
                       timed: bool = True) -> Tuple[Any, Optional[date]]:
        """A parent key for a child dated ``child_anchor``.

        Zipf-skewed over the parents that existed by then (oldest = hottest — a
        long-standing customer has the most transactions).  Returns the value and,
        when the child predates every parent, the earliest parent's anchor so the
        caller can lift the child to it.  ``timed=False`` (reference tables) ignores
        time — a 2023 payment may name any currency, not only the first three.
        """
        n = len(parent_rows)
        if n == 0 or not timeline:
            return None, None
        sorted_anchors, order_idx = timeline
        lifted: Optional[date] = None
        if timed:
            k = bisect.bisect_right(sorted_anchors, child_anchor)
            if k == 0:
                k, lifted = 1, sorted_anchors[0]
        else:
            k = n
        pos = min(k - 1, int(k * (rng.random() ** 2.2)))
        idx = order_idx[pos]
        val = parent_rows[idx].get(pcol)
        if val is None:                        # that parent has no key value: uniform retry
            for _ in range(4):
                idx = order_idx[rng.randrange(k)]
                val = parent_rows[idx].get(pcol)
                if val is not None:
                    break
        return val, lifted

    @staticmethod
    def _sample_uniform(rows, pcol, rng) -> Any:
        if not rows:
            return None
        return rows[rng.randrange(len(rows))].get(pcol)

    @staticmethod
    def _draw_anchor(rng: random.Random) -> date:
        days_ago = int(HORIZON_DAYS * (rng.random() ** _RECENCY_EXP))
        return TODAY - timedelta(days=days_ago)

    # ── primary keys ─────────────────────────────────────────────────────────

    @staticmethod
    def _pk_style(table: PhysTable, col: PhysColumn) -> Callable[[int], Any]:
        t = (col.dbx_type or "").upper()
        if t.startswith(("BIGINT", "INT", "SMALLINT", "TINYINT", "LONG")):
            return lambda i: i + 1
        toks = [x for x in _tokens(table.logical or table.name) if x not in ("id", "key")]
        abbr = "".join(x[0] for x in toks[:4]).upper() or "REC"
        if len(toks) == 1:
            abbr = toks[0][:4].upper()
        return lambda i: f"{abbr}-{i + 1:06d}"

    # ── column plans ─────────────────────────────────────────────────────────

    def _plan_column(self, table: PhysTable, col: PhysColumn) -> Gen:
        toks = _tokens(col.name)
        t = (col.dbx_type or "STRING").upper().strip()
        desc = (col.comment or "").lower()
        nullable_p = self._null_probability(col, toks)

        # a) the author's own declaration (transliterated — an author's typo such as a
        #    dotless i must not become a non-LATIN value in the warehouse)
        rx = _ascii((col.value_regex or "").strip())
        if rx and regex_gen.is_alternation(rx):
            opts = regex_gen.alternation_options(rx)
            if opts:
                self._note(table, col, f"declared list ({len(opts)} values, weighted)")
                base: Gen = self._weighted_pick(opts)
                return self._nullable(self._typed(base, t, toks), nullable_p)
        if rx:
            iso = self._iso_vocab_for(rx, toks, desc)
            if iso is not None:
                self._note(table, col, "declared shape + a real ISO vocabulary")
                return self._nullable(self._typed(iso, t, toks), nullable_p)
            if regex_gen.supports(rx):
                if "@" in rx:
                    self._note(table, col, "declared e-mail shape (derived from the row's name)")
                    return self._nullable(self._typed(self._email(rx), t, toks), nullable_p)
                self._note(table, col, "declared shape (regex-generated)")
                return self._nullable(self._typed(self._from_regex(rx, toks), t, toks), nullable_p)

        # b) name + type — a STRONG reading (identity, place, money, time …) wins;
        #    a WEAK one (generic label / code / categorical-by-token) yields to the
        #    examples the author wrote into the description ("e.g., T24, FIS_PROFILE")
        gen, weak = self._by_name_and_type(table, col, toks, t, desc)
        if weak and t == "STRING":
            examples = self._examples_from_description(col.comment or "")
            if examples:
                self._note(table, col, f"examples from the description ({len(examples)})")
                return self._nullable(self._weighted_pick(examples), nullable_p)
        return self._nullable(gen, nullable_p)

    # -- helpers used by the plans -------------------------------------------

    @staticmethod
    def _nullable(gen: Gen, p: float) -> Gen:
        if p <= 0:
            return gen
        def g(rng, ctx):
            return None if rng.random() < p else gen(rng, ctx)
        return g

    def _null_probability(self, col: PhysColumn, toks: Sequence[str]) -> float:
        if not col.nullable or col.is_primary_key:
            return 0.0
        if (col.dbx_type or "").upper() in ("BOOLEAN", "BOOL"):
            return 0.0            # deployed as BYTEINT NOT NULL DEFAULT 0 — a NULL fails the load
        if _has(toks, "end", "close", "closed", "termination", "terminated", "expiry", "expiration",
                "resolved", "resolution", "cancellation", "cancelled", "completion", "completed",
                "deleted", "deactivation", "settlement", "middle", "fax", "secondary", "alternate",
                "alternative", "notes", "comment", "comments", "remarks") and not _endswith(toks, "flag", "indicator"):
            return 0.35
        if _has(toks, "name", "status", "code", "amount", "date", "type", "id", "number", "key",
                "count", "quantity", "price", "total", "rate", "currency", "created", "timestamp"):
            return 0.0
        return self.null_rate

    @staticmethod
    def _typed(gen: Gen, t: str, toks: Sequence[str]) -> Gen:
        """A string-shaped generator adapted to a non-string column when needed."""
        if t == "STRING" or t.startswith(("VARCHAR", "CHAR")):
            return gen
        if t.startswith(("BIGINT", "INT", "SMALLINT", "TINYINT", "LONG")):
            def gi(rng, ctx):
                v = gen(rng, ctx)
                digits = re.sub(r"\D", "", str(v))
                return int(digits[:9]) if digits else rng.randint(1, 999)
            return gi
        if t.startswith(("DECIMAL", "NUMERIC", "NUMBER", "DOUBLE", "FLOAT", "REAL")):
            def gd(rng, ctx):
                v = gen(rng, ctx)
                m = re.search(r"\d+(\.\d+)?", str(v))
                return Decimal(m.group(0)) if m else Decimal(rng.randint(1, 999))
            return gd
        return gen

    @staticmethod
    def _weighted_pick(options: Sequence[str]) -> Gen:
        # Geometric-ish weights in the author's order (the first option is usually
        # the common case: active, low, pending …) with a floor so EVERY option
        # appears — a declared value that never occurs is a demo that lies by omission.
        weights = [0.55 ** i + 0.08 for i in range(len(options))]
        opts = list(options)
        def g(rng, ctx):
            return rng.choices(opts, weights=weights, k=1)[0]
        return g

    @staticmethod
    def _iso_vocab_for(rx: str, toks: Sequence[str], desc: str) -> Optional[Gen]:
        shape = rx.strip().strip("^$")
        country = _has(toks, "country", "nationality", "citizenship", "domicile", "jurisdiction") or "iso 3166" in desc
        currency = _has(toks, "currency", "ccy") or "iso 4217" in desc
        language = _has(toks, "language", "lang") or "iso 639" in desc
        if shape in ("[A-Z]{3}", "[A-Z]{3,3}"):
            if currency:
                return lambda rng, ctx: ctx.place[6] if rng.random() < 0.8 else rng.choice(vocab.CURRENCIES)
            if country:
                return lambda rng, ctx: ctx.place[5]
        if shape in ("[A-Z]{2}", "[A-Z]{2,2}") and country:
            return lambda rng, ctx: ctx.place[4]
        if shape in ("[a-z]{2}", "[a-z]{2,2}") and language:
            return lambda rng, ctx: rng.choice(vocab.LANGUAGES)
        if shape in ("[a-z]{2}(-[A-Z]{2})?", "[a-z]{2}-[A-Z]{2}") and language:
            return lambda rng, ctx: rng.choice(vocab.LANGUAGE_TAGS)
        return None

    @staticmethod
    def _from_regex(rx: str, toks: Sequence[str]) -> Gen:
        def g(rng, ctx):
            v = regex_gen.generate(rx, rng)
            return v if v is not None else f"{'_'.join(toks)[:12].upper() or 'VAL'}-{ctx.i + 1:05d}"
        return g

    @staticmethod
    def _email(rx: Optional[str] = None) -> Gen:
        def g(rng, ctx):
            local = f"{ctx.first}.{ctx.last}".lower().replace("'", "")
            if rng.random() < 0.3:
                local += str(rng.randint(1, 99))
            v = f"{local}@{rng.choice(vocab.EMAIL_DOMAINS)}"
            if rx and not re.fullmatch(rx, v):
                v = regex_gen.generate(rx, rng) or v
            return v
        return g

    @staticmethod
    def _examples_from_description(desc: str) -> List[str]:
        m = re.search(r"(?:e\.g\.,?|such as|for example,?|examples?:)\s*([^.;\n]+)", desc, re.I)
        if not m:
            return []
        body = m.group(1)
        body = re.sub(r"\band\b|\bor\b|\betc\.?", ",", body)
        items: List[str] = []
        for raw in body.split(","):
            item = raw.strip().strip("()'\"")
            item = re.split(r"\s+for\s+|\s*\(|\s*[-–—]\s", item)[0].strip()
            if not item or len(item) > 30 or len(item.split()) > 3:
                continue
            if re.fullmatch(r"[A-Za-z0-9_./ -]+", item):
                items.append(item)
        seen: List[str] = []
        for it in items:
            if it not in seen:
                seen.append(it)
        return seen if len(seen) >= 2 else []

    # -- name + type -----------------------------------------------------------

    def _by_name_and_type(self, table: PhysTable, col: PhysColumn, toks: Sequence[str],
                          t: str, desc: str) -> Tuple[Gen, bool]:
        """(generator, weak) — ``weak`` marks a generic fallback the description may override."""
        note = lambda s: self._note(table, col, s)  # noqa: E731

        if t in ("BOOLEAN", "BOOL"):
            p = self._bool_probability(toks)
            note(f"flag (true {int(p * 100)}%)")
            return (lambda rng, ctx: 1 if rng.random() < p else 0), False

        if t == "DATE" or t.startswith("TIMESTAMP"):
            return self._date_gen(toks, t.startswith("TIMESTAMP"), note), False

        if t.startswith(("BIGINT", "INT", "SMALLINT", "TINYINT", "LONG")):
            return self._int_gen(toks, t, note), False

        if t.startswith(("DECIMAL", "NUMERIC", "NUMBER", "DOUBLE", "FLOAT", "REAL")):
            return self._number_gen(toks, t, note), False

        if t.startswith(("ARRAY", "MAP", "STRUCT")):
            note("complex type → empty collection")
            return (lambda rng, ctx: "[]"), False

        return self._string_gen(table, toks, desc, note)

    @staticmethod
    def _bool_probability(toks: Sequence[str]) -> float:
        if _has(toks, "deleted", "cancelled", "canceled", "blocked", "suspended", "expired", "fraud",
                "fraudulent", "disputed", "overdue", "default", "defaulted", "rejected", "failed",
                "error", "locked", "restricted", "sanctioned", "pep", "deceased", "revoked",
                "terminated", "void", "voided", "written", "writeoff", "impaired"):
            return 0.08
        if _has(toks, "active", "enabled", "valid", "verified", "approved", "current", "primary",
                "eligible", "compliant", "confirmed", "available", "open", "success", "successful",
                "consent", "opted"):
            return 0.85
        if _has(toks, "required", "mandatory", "test", "manual", "override", "exception", "escalated",
                "priority", "urgent", "recurring", "international", "cross", "partial"):
            return 0.25
        return 0.35

    @staticmethod
    def _date_gen(toks: Sequence[str], is_ts: bool, note) -> Gen:
        def at(d: date, rng: random.Random):
            if not is_ts:
                return d
            return datetime(d.year, d.month, d.day, rng.randint(6, 21), rng.randint(0, 59), rng.randint(0, 59))

        if _has(toks, "birth", "dob", "born"):
            note("date of birth (18–90 years before the row's anchor)")
            return lambda rng, ctx: at(ctx.anchor - timedelta(days=rng.randint(18 * 365, 90 * 365)), rng)
        if _has(toks, "incorporation", "incorporated", "founded", "founding", "established", "establishment",
                "commencement", "original", "first", "initial", "acquisition", "acquired", "installation",
                "installed", "manufacture", "manufactured", "build", "built"):
            note("origin date (1–25 years before the row's anchor)")
            return lambda rng, ctx: at(ctx.anchor - timedelta(days=rng.randint(365, 25 * 365)), rng)
        if _has(toks, "expiry", "expiration", "expires", "maturity", "renewal", "valid", "until",
                "due", "end", "close", "closed", "termination", "terminated", "target", "planned",
                "scheduled", "next", "review"):
            note("later date (after the anchor, may lie in the future)")
            def later(rng, ctx):
                span = rng.randint(1, 5 * 365) if _has(toks, "expiry", "expiration", "expires",
                                                          "maturity", "renewal", "until", "valid") else rng.randint(1, 400)
                return at(ctx.anchor + timedelta(days=span), rng)
            return later
        if _has(toks, "updated", "modified", "changed", "last", "latest", "completed", "resolved",
                "settlement", "settled", "delivered", "delivery", "paid", "payment", "posted",
                "posting", "approved", "approval", "processed", "processing", "actual"):
            note("follow-up date (anchor + up to 120 days, never after today)")
            return lambda rng, ctx: at(min(TODAY, ctx.anchor + timedelta(days=int(120 * rng.random() ** 2))), rng)
        if _has(toks, "created", "creation", "transaction", "order", "ordered", "booking", "booked",
                "event", "start", "started", "open", "opened", "opening", "effective", "issue", "issued",
                "issuance", "entry", "posting", "record", "received", "submitted", "submission",
                "request", "requested", "activation", "enrollment", "registration", "signup",
                "onboarding", "hire", "hired", "inception", "origination", "initiated", "trade",
                "value", "as", "snapshot", "report", "reporting", "statement", "period", "business",
                "calendar"):
            note("the row's anchor date (trend-weighted toward recent; after its parents)")
            return lambda rng, ctx: at(ctx.anchor, rng)
        note("a date shortly after the row's anchor (0–45 days)")
        return lambda rng, ctx: at(min(TODAY, ctx.anchor + timedelta(days=rng.randint(0, 45))), rng)

    @staticmethod
    def _int_gen(toks: Sequence[str], t: str, note) -> Gen:
        hi = 127 if t.startswith("TINYINT") else 32767 if t.startswith("SMALLINT") else 2_000_000_000
        def bounded(v: int) -> int:
            return max(0, min(hi, int(v)))
        if _has(toks, "year") and not _has(toks, "years"):
            note("calendar year"); return lambda rng, ctx: bounded(ctx.anchor.year)
        if _has(toks, "month") and not _has(toks, "months"):
            note("calendar month"); return lambda rng, ctx: bounded(ctx.anchor.month)
        if _has(toks, "quarter"):
            note("quarter"); return lambda rng, ctx: bounded((ctx.anchor.month - 1) // 3 + 1)
        if _has(toks, "week") and not _has(toks, "weeks"):
            note("week of year"); return lambda rng, ctx: bounded(ctx.anchor.isocalendar()[1])
        if _has(toks, "day") and not _has(toks, "days"):
            note("day of month"); return lambda rng, ctx: bounded(ctx.anchor.day)
        if _has(toks, "age"):
            note("age in years"); return lambda rng, ctx: bounded(rng.randint(18, 88))
        if _has(toks, "days", "hours", "minutes", "months", "weeks", "years", "duration", "tenure"):
            note("duration (geometric)"); return lambda rng, ctx: bounded(int(rng.expovariate(1 / 40.0)) + 1)
        if _has(toks, "score", "rating", "rank", "ranking", "index"):
            note("score (bell-shaped)"); return lambda rng, ctx: bounded(int(min(100, max(0, rng.gauss(62, 18)))))
        if _endswith(toks, "id", "key", "number", "no", "ref", "reference", "identifier"):
            note("external identifier (7–9 digits)"); return lambda rng, ctx: bounded(rng.randint(1_000_000, 999_999_999))
        if _has(toks, "count", "quantity", "qty", "units", "seats", "capacity", "size",
                "volume", "frequency", "attempts", "retries", "occurrences", "items", "lines", "children",
                "dependents", "employees", "headcount", "passengers", "visits"):
            note("count (geometric, small)"); return lambda rng, ctx: bounded(int(rng.expovariate(1 / 6.0)) + 1)
        if _has(toks, "percent", "percentage", "pct"):
            note("percentage 0–100"); return lambda rng, ctx: bounded(int(min(100, rng.expovariate(1 / 25.0))))
        if _has(toks, "version", "revision", "sequence", "seq", "order", "position", "priority", "level"):
            note("small ordinal"); return lambda rng, ctx: bounded(rng.randint(1, 9))
        if _has(toks, "amount", "balance", "value", "revenue", "cost", "price", "fee", "limit", "total",
                "budget", "income", "salary", "premium", "principal", "spend", "sales", "charge", "payment"):
            note("money as integer (log-normal)"); return lambda rng, ctx: bounded(int(math.exp(rng.gauss(7.5, 1.2))))
        note("integer (geometric)"); return lambda rng, ctx: bounded(int(rng.expovariate(1 / 120.0)) + 1)

    @staticmethod
    def _number_gen(toks: Sequence[str], t: str, note) -> Gen:
        p, s = _decimal_spec(t) if t.startswith(("DECIMAL", "NUMERIC", "NUMBER")) else (18, 4)
        is_float = not t.startswith(("DECIMAL", "NUMERIC", "NUMBER"))
        def out(v: float):
            return round(float(v), 4) if is_float else _fit_decimal(v, p, s)
        if _has(toks, "latitude", "lat"):
            note("latitude"); return lambda rng, ctx: out(rng.uniform(-55, 70))
        if _has(toks, "longitude", "lon", "lng"):
            note("longitude"); return lambda rng, ctx: out(rng.uniform(-125, 150))
        fraction = (p - s) <= 1
        if _has(toks, "rate", "apr", "apy", "yield", "interest", "coupon", "margin", "spread"):
            note("rate (small, right-skewed)")
            return lambda rng, ctx: out(min(0.35, rng.expovariate(1 / 0.045)) if fraction else min(35.0, rng.expovariate(1 / 4.5)))
        if _has(toks, "pct", "percent", "percentage", "ratio", "share", "proportion", "discount",
                "allocation", "probability", "likelihood", "confidence", "utilization", "utilisation"):
            note("proportion")
            return lambda rng, ctx: out(min(1.0, rng.betavariate(2, 5)) if fraction else min(100.0, rng.betavariate(2, 5) * 100))
        if _has(toks, "score", "rating"):
            note("score (bell-shaped)")
            return lambda rng, ctx: out(min(5.0, max(1.0, rng.gauss(3.6, 0.8))) if (p - s) <= 1 else min(100.0, max(0.0, rng.gauss(62, 18))))
        if _has(toks, "quantity", "qty", "units", "count", "volume", "hours", "weight", "kg", "length", "distance", "area"):
            note("measured quantity (log-normal, small)"); return lambda rng, ctx: out(math.exp(rng.gauss(2.2, 0.9)))
        big = _has(toks, "balance", "total", "revenue", "budget", "limit", "principal", "exposure", "outstanding",
                   "valuation", "assets", "liabilities", "equity", "capital", "aum", "portfolio", "notional",
                   "salary", "income", "premium", "annual", "gross", "net")
        small = _has(toks, "unit", "price", "fee", "charge", "tax", "commission", "surcharge", "per", "hourly", "daily")
        mu = 10.5 if big else 4.2 if small else 7.2
        note("money (log-normal, " + ("large" if big else "small" if small else "medium") + ")")
        return lambda rng, ctx: out(math.exp(rng.gauss(mu, 1.1)))

    def _string_gen(self, table: PhysTable, toks: Sequence[str], desc: str, note) -> Tuple[Gen, bool]:
        strong, weak = self._string_gen_inner(table, toks, desc, note)
        return strong, weak

    def _string_gen_inner(self, table: PhysTable, toks: Sequence[str], desc: str, note) -> Tuple[Gen, bool]:
        ttoks = _tokens(table.logical or table.name)
        person_table = _has(ttoks, "party", "customer", "person", "employee", "contact", "individual",
                            "patient", "member", "user", "agent", "borrower", "insured", "subscriber",
                            "passenger", "driver", "applicant", "beneficiary", "policyholder", "guest",
                            "student", "worker", "staff", "advisor", "physician", "provider")
        # identity
        if _has(toks, "first", "given", "forename") and _has(toks, "name"):
            note("first name"); return (lambda rng, ctx: ctx.first), False
        if _has(toks, "last", "surname", "family") and _has(toks, "name"):
            note("last name"); return (lambda rng, ctx: ctx.last), False
        if _has(toks, "middle") and _has(toks, "name"):
            note("middle initial"); return (lambda rng, ctx: rng.choice("ABCDEJKLMRST") + "."), False
        if _has(toks, "full", "display", "legal", "contact", "person", "customer", "employee", "holder",
                "owner", "applicant", "insured", "patient", "member", "user", "signatory", "beneficiary") and _has(toks, "name"):
            if _has(toks, "legal", "company", "business", "organization", "organisation", "entity") and not person_table:
                note("company name"); return (lambda rng, ctx: ctx.company), False
            note("person's full name"); return (lambda rng, ctx: f"{ctx.first} {ctx.last}"), False
        if _has(toks, "company", "employer", "organization", "organisation", "vendor", "supplier", "merchant",
                "manufacturer", "carrier", "counterparty", "issuer", "institution", "bank", "insurer",
                "brand", "operator", "provider", "partner", "firm", "agency", "dealer") and _has(toks, "name"):
            note("company name"); return (lambda rng, ctx: ctx.company), False
        if _has(toks, "email", "mail"):
            note("e-mail (from the row's name)"); return (self._email()), False
        if _has(toks, "phone", "mobile", "telephone", "fax", "tel", "cell"):
            note("phone (row's country prefix)")
            return (lambda rng, ctx: f"{ctx.place[8]} {rng.randint(200, 989)} {rng.randint(100, 999)} {rng.randint(1000, 9999)}"), False
        if _has(toks, "url", "website", "web", "link", "homepage"):
            note("URL"); return (lambda rng, ctx: "https://www." + re.sub(r"[^a-z]", "", ctx.company.lower())[:16] + ".com"), False
        if _has(toks, "iban"):
            note("IBAN shape"); return (lambda rng, ctx: f"{ctx.place[4]}{rng.randint(10, 99)}{''.join(str(rng.randint(0, 9)) for _ in range(18))}"), False
        if _has(toks, "bic", "swift"):
            note("BIC shape"); return (lambda rng, ctx: "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(4)) + ctx.place[4] + rng.choice(["2X", "33", "MM", "22"])), False
        # place
        if _has(toks, "city", "town", "municipality"):
            note("city (row's place)"); return (lambda rng, ctx: ctx.place[0]), False
        place_state = _has(toks, "province", "region") or (
            "state" in toks and _has(toks, "address", "residence", "code", "name", "home", "mailing",
                                     "billing", "shipping", "registration", "domicile", "of", "issuing", "tax"))
        if place_state and not _has(toks, "status", "machine", "lifecycle"):
            if _has(toks, "code", "abbr", "abbreviation"):
                note("region code"); return (lambda rng, ctx: ctx.place[1]), False
            note("region name"); return (lambda rng, ctx: ctx.place[2]), False
        if _has(toks, "country", "nationality", "citizenship", "domicile", "jurisdiction"):
            if _has(toks, "code", "iso", "alpha") or (desc and "alpha-3" in desc):
                note("ISO country code"); return (lambda rng, ctx: ctx.place[5] if ("alpha-3" in desc or "3" in " ".join(toks)) else ctx.place[4]), False
            note("country name"); return (lambda rng, ctx: ctx.place[3]), False
        if _has(toks, "postal", "postcode", "zip", "zipcode"):
            note("postal code (row's country)"); return (lambda rng, ctx: _postal(ctx.place[7], rng)), False
        if _has(toks, "street", "address") and not _has(toks, "email", "ip", "mac", "type", "status"):
            if _has(toks, "line") and _has(toks, "2", "two", "second"):
                note("address line 2"); return (lambda rng, ctx: rng.choice(["Suite", "Floor", "Unit", "Building"]) + " " + str(rng.randint(1, 40))), False
            note("street address"); return (lambda rng, ctx: f"{rng.randint(1, 999)} {rng.choice(vocab.STREETS)}"), False
        if _has(toks, "calling", "dialing", "dial") and _has(toks, "code", "prefix"):
            note("phone prefix (row's country)"); return (lambda rng, ctx: ctx.place[8]), False
        if _has(toks, "timezone", "tz") or ("time" in toks and "zone" in toks):
            note("time zone"); return (lambda rng, ctx: ctx.place[9]), False
        if _has(toks, "currency", "ccy"):
            note("currency (row's country, USD-heavy)"); return (lambda rng, ctx: ctx.place[6] if rng.random() < 0.8 else rng.choice(vocab.CURRENCIES)), False
        if _has(toks, "language", "lang", "locale"):
            note("language code"); return (lambda rng, ctx: rng.choice(vocab.LANGUAGE_TAGS if _has(toks, "locale") else vocab.LANGUAGES)), False
        # product-ish names
        if _endswith(toks, "name", "title", "label") or _has(toks, "name"):
            if person_table and not _has(toks, "product", "plan", "account", "policy", "campaign", "project", "asset", "device", "file", "report", "document"):
                note("person's full name"); return (lambda rng, ctx: f"{ctx.first} {ctx.last}"), False
            stem = " ".join(x.capitalize() for x in ttoks[:2]) or "Item"
            note(f"product-style name ('{stem} …')")
            return (lambda rng, ctx: f"{rng.choice(vocab.PRODUCT_ADJECTIVES)} {stem} {rng.choice(vocab.PRODUCT_NOUNS)} {rng.randint(100, 999)}"), False
        if _endswith(toks, "code") and not _has(toks, "postal", "zip", "country", "currency", "language"):
            prefix = "".join(x[0] for x in toks[:3]).upper() or "CD"
            note(f"code ({prefix}-nnnnnn)")
            return (lambda rng, ctx: f"{prefix}-{''.join(str(rng.randint(0, 9)) for _ in range(6))}"), True
        if _has(toks, "department", "division", "team", "unit") and not _has(toks, "id", "code"):
            note("department"); return (lambda rng, ctx: rng.choice(vocab.DEPARTMENTS)), False
        if _has(toks, "job", "position", "occupation", "profession") or (_has(toks, "title") and person_table):
            note("job title"); return (lambda rng, ctx: rng.choice(vocab.JOB_TITLES)), False
        # dates and periods stored as text
        if _has(toks, "date", "timestamp", "datetime"):
            note("date as text (ISO)"); return (lambda rng, ctx: (ctx.anchor.isoformat() if not _has(toks, "timestamp", "datetime") else f"{ctx.anchor.isoformat()}T{rng.randint(6, 21):02d}:{rng.randint(0, 59):02d}:00")), False
        if _has(toks, "year") and not _has(toks, "years"):
            note("year as text"); return (lambda rng, ctx: str(ctx.anchor.year)), False
        if _has(toks, "month") and not _has(toks, "months"):
            note("month as text (YYYY-MM)"); return (lambda rng, ctx: f"{ctx.anchor.year}-{ctx.anchor.month:02d}"), False
        # free text
        if _has(toks, "description", "notes", "note", "comment", "comments", "remarks", "remark", "reason",
                "text", "message", "summary", "narrative", "explanation", "justification", "details", "purpose",
                "instructions", "feedback", "rationale", "observation", "observations", "resolution"):
            note("sentence (curated)"); return (lambda rng, ctx: rng.choice(vocab.SENTENCES)), False
        # time-of-day / version / period as strings
        if _endswith(toks, "time") and not _has(toks, "zone"):
            note("time of day"); return (lambda rng, ctx: f"{rng.randint(6, 21):02d}:{rng.choice(['00', '15', '30', '45'])}"), False
        if _has(toks, "version"):
            note("version"); return (lambda rng, ctx: f"{rng.randint(1, 4)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}"), False
        if _has(toks, "period", "fiscal") and _has(toks, "year", "period", "quarter", "month"):
            note("fiscal period"); return (lambda rng, ctx: (f"FY{ctx.anchor.year}" if _has(toks, "year") else f"{ctx.anchor.year}-Q{(ctx.anchor.month - 1) // 3 + 1}" if _has(toks, "quarter") else f"{ctx.anchor.year}-{ctx.anchor.month:02d}")), False
        if _has(toks, "hash", "token", "secret", "signature", "checksum", "digest"):
            note("hex token"); return (lambda rng, ctx: "".join(rng.choice("0123456789abcdef") for _ in range(32))), False
        if _has(toks, "ip"):
            note("IPv4"); return (lambda rng, ctx: f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"), False
        # categorical by the LAST token (status / type / category / channel …)
        for tok in reversed(list(toks)):
            words = vocab.VOCAB_BY_TOKEN.get(tok)
            if words:
                note(f"categorical by name ('{tok}')"); return (self._weighted_pick(words)), True
        # codes / references / identifiers
        if _endswith(toks, "code", "number", "no", "num", "reference", "ref", "identifier", "id", "key", "sku", "isbn", "vin", "imei", "iccid", "msisdn", "ean", "upc"):
            prefix = "".join(x[0] for x in toks[:3]).upper() or "REF"
            width = 8 if _endswith(toks, "number", "no", "num", "vin", "imei", "iccid", "msisdn", "ean", "upc") else 6
            note(f"code ({prefix}-nnnnnn)")
            return (lambda rng, ctx: f"{prefix}-{''.join(str(rng.randint(0, 9)) for _ in range(width))}"), True
        # default: a readable label
        stem = " ".join(x.capitalize() for x in toks[:3]) or "Value"
        note(f"label ('{stem} n')")
        return (lambda rng, ctx: f"{stem} {rng.randint(1, 999)}"), True

    # ── within-row date ordering ─────────────────────────────────────────────

    @staticmethod
    def _order_pairs(table: PhysTable) -> List[Tuple[str, str]]:
        dates = [c for c in table.columns if (c.dbx_type or "").upper().startswith(("DATE", "TIMESTAMP"))]
        by_toks = {c.name: set(_tokens(c.name)) for c in dates}
        pairs: List[Tuple[str, str]] = []
        earlier_words = {"created", "creation", "start", "started", "open", "opened", "effective", "issue",
                         "issued", "from", "begin", "inception", "onboarding", "hire", "hired", "order",
                         "ordered", "transaction", "booking", "booked", "request", "requested", "received",
                         "submitted", "submission", "initiated", "origination", "entry", "enrollment",
                         "registration", "signup", "activation"}
        later_words = {"updated", "modified", "end", "ended", "close", "closed", "expiry", "expiration",
                       "expires", "maturity", "termination", "terminated", "completed", "completion",
                       "resolved", "resolution", "settlement", "settled", "delivered", "delivery",
                       "paid", "payment", "approved", "approval", "processed", "cancelled", "cancellation",
                       "renewal", "until", "to", "due", "last", "latest", "actual", "posted", "posting"}
        earlier = [c.name for c in dates if by_toks[c.name] & earlier_words]
        later = [c.name for c in dates if by_toks[c.name] & later_words]
        for e in earlier:
            for l in later:
                if e != l:
                    pairs.append((e, l))
        return pairs


# ── small pure helpers ────────────────────────────────────────────────────────

def _as_date(v: Any) -> date:
    return v.date() if isinstance(v, datetime) else v


def _precedes(b: Any, a: Any) -> bool:
    """True when ``b`` lies before ``a`` — compared as datetimes when both are, else as dates."""
    if isinstance(a, datetime) and isinstance(b, datetime):
        return b < a
    return _as_date(b) < _as_date(a)


def _shift_like(later_val: Any, earlier_val: Any, rng: random.Random) -> Any:
    """Move ``later`` to a point AFTER ``earlier`` keeping its own type."""
    if isinstance(later_val, datetime):
        base = earlier_val if isinstance(earlier_val, datetime) else datetime(earlier_val.year, earlier_val.month, earlier_val.day, 9, 0, 0)
        return base + timedelta(hours=rng.randint(1, 24 * 60))
    return _as_date(earlier_val) + timedelta(days=rng.randint(0, 60))


def _postal(shape: str, rng: random.Random) -> str:
    if not shape:
        return ""
    out = []
    for ch in shape:
        if ch == "#":
            out.append(str(rng.randint(0, 9)))
        elif ch == "A":
            out.append(rng.choice("ABCDEFGHJKLMNPRSTVWXYZ"))
        else:
            out.append(ch)
    return "".join(out)


