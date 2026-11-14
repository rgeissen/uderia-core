# 🤝 Intelligence Marketplace: Collaborative Knowledge Sharing

The Intelligence Marketplace is where execution patterns, domain knowledge, agent teams, skills, extensions and knowledge graphs are shared between users and organisations. Proven work in one place becomes the starting point in another, which lowers token cost, shortens onboarding and spreads capability.

## What is the Intelligence Marketplace?

The marketplace is Uderia's channel for sharing, discovering and deploying agent assets. Community-validated strategies, domain knowledge and agent configurations circulate between users, reducing token cost through proven patterns, and a published asset can be forked and improved.

**Product Catalog** — Seven interconnected asset types:

| Product Type | Description | Acquisition |
|---|---|---|
| **Planner Repositories** (📋) | Proven execution patterns and strategies for autonomous task completion | Subscribe, Fork |
| **Knowledge Repositories** (📄) | Reference documents and domain knowledge for planning context | Subscribe, Fork |
| **Agent Packs** | Bundled agent teams (coordinator, experts, knowledge collections) as portable `.agentpack` files | Subscribe, Fork |
| **Skills** | Pre-processing prompt injections that modify LLM behavior (Claude Code compatible format) | Install |
| **Extensions** | Reusable processing modules with tiered complexity (convention, simple, standard, LLM) | Install |
| **Knowledge Graphs** | Entity-relationship models for database topology, business concepts, and domain ontologies | Subscribe, Install, Fork |
| **Component instances** | Flows and dashboards published as data (definition/spec), never code | Subscribe (manual sync), Fork |

## Core Marketplace Features

**Smart Discovery & Search**

Find what you need with search and filtering:
- Keyword search across collection names and descriptions
- Filter by repository type (Planner vs. Knowledge)
- Filter by visibility (Public, Targeted), rating, and install counts
- Sort by popularity, rating, or recency
- Pagination for browsing large catalogs
- View metadata: owner, subscriber count, ratings, case/document counts

**Reference-Based Subscriptions**

Access shared collections without data duplication:
- Subscribe to expert-curated collections with one click
- Automatic integration into your RAG system
- The planner retrieves cases from subscribed collections as it does from its own
- No storage overhead—references original collection
- Unsubscribe anytime to manage your collection portfolio
- Live updates when publishers improve collections

**Fork for Customization**

Create independent copies for your specific needs:
- Full copy including embeddings, files, and metadata
- Customize forked collections without affecting originals
- Use it to adapt community patterns to your domain
- Iterative refinement through fork-and-improve workflow
- Build on proven strategies while maintaining independence

**Community Quality Assurance**

Trust community validation through ratings and reviews:
- 1-5 star rating system with optional text reviews
- Average ratings displayed on asset cards
- Cannot rate own assets (ensures objectivity)
- Browse top-rated assets for proven quality
- Community feedback guides discovery

**Flexible Publishing Options**

Share your expertise with granular visibility control:
- **Public:** Fully discoverable in marketplace browse
- **Targeted:** Visible only to specific users granted explicit access (via per-user sharing grants)
- **Private:** Owner-only, unpublished (default)
- Update visibility anytime
- A knowledge graph must have entities before it can be published; the Default Collection cannot be published
- Maintain full ownership and control

**Secure Access Control**

Authorization and privacy:
- JWT-authenticated API endpoints
- Ownership validation on all operations
- Cannot subscribe to own assets
- Must be owner to publish or modify
- Usernames visible for transparency and attribution
- Privacy-first design with granular visibility controls

**REST API Integration**

Programmatic marketplace operations for automation:
- Browse assets with search/filter parameters
- Subscribe/unsubscribe programmatically
- Fork assets via API for CI/CD workflows
- Publish assets as part of deployment pipelines
- Rate assets for automated quality tracking
- Full CRUD operations for marketplace management

## Agent Packs: Portable AI Teams

Agent Packs bundle complete agent configurations—coordinator profiles, expert profiles, knowledge collections, and MCP server references—into a single `.agentpack` file that can be installed, exported, and shared across environments.

**Why Agent Packs matter:** Building a well-tuned multi-profile agent team (e.g., a Genie coordinator with specialized RAG experts) requires significant effort: creating profiles, assigning knowledge collections, configuring child relationships, and testing the whole ensemble. Agent Packs capture this investment as a portable, versioned artifact that can be deployed in seconds on any Uderia instance.

**How to use Agent Packs:**

* **Install:** Import an `.agentpack` file via Anatomy → Agent Packs → Import. All profiles, collections, and dependencies are created automatically with conflict resolution.
* **Export:** Select an installed pack and click Export to produce a `.agentpack` file containing the current live state of all profiles and collections.
* **Publish:** Share your pack to the Intelligence Marketplace for other users to discover and install with one click.
* **Harmonize LLM:** After importing a pack, use the Harmonize LLM feature to switch all pack profiles to your preferred LLM provider in one operation.
* **Live References:** Packs store references to profiles and collections, not copies. Changes you make to pack-managed profiles (e.g., adding an expert to a coordinator) take effect immediately and are captured in subsequent exports.
* **Republish without breaking subscribers:** re-importing with `update_in_place` (the default) keeps every installation, listing, profile and collection ID, diffs documents at chunk level and refreshes pack-owned knowledge graphs in place; `dry_run` previews the change. `replace` (fresh IDs) and `expand` (side-by-side install with renamed tags) are the alternatives.

**Packs as governed data products.** A pack moves through a release lifecycle — **draft → propose → approve → publish → deprecate** — with an SLA tier recorded on the installation. The publish gate reads the pack's trust state, so an admin can require approval and refuse to publish an untrusted pack; every lifecycle attempt, allowed or denied, lands in an append-only audit table, and a deprecated pack can no longer be forked. The default policy (no approval, untrusted publish allowed) leaves today's behaviour unchanged. [Architecture →](../Architecture/PACK_GOVERNANCE_ARCHITECTURE.md)

Agent Packs turn the collaborative marketplace into a distribution channel for complete AI solutions—not just individual knowledge repositories, but fully operational agent teams ready for production use.

## Why the Marketplace Matters

**Cost Reduction Through Reuse**

Leverage proven patterns to minimize token consumption:
- Reuse champion execution strategies instead of trial-and-error
- Access domain expertise without rebuilding from scratch
- Community-validated patterns reduce failed attempts
- Lower onboarding costs for new users and use cases
- Network effects: more users = more valuable patterns

**Collaborative Intelligence**

The marketplace turns Uderia from a single-user tool into a shared one. Pattern sharing, community validation and knowledge reuse reduce cost, improve quality and shorten time to value, whether you publish your own expertise or subscribe to someone else's.

## Getting Started with the Marketplace

**Discovering Assets:**
1. Navigate to **Marketplace** (house icon)
2. Select the product type tab (Planner Repositories, Knowledge Repositories, Agent Packs, etc.)
3. Use search and filters to find relevant assets
4. View ratings, subscriber counts, and metadata before subscribing

**Subscribing to Collections:**
- Click **Subscribe** on a collection card for reference-based access
- Subscribed collections automatically integrate into your RAG system
- View your subscriptions in the **My Collections** tab

**Forking for Customization:**
- Click **Fork** to create an independent copy
- Customize the fork without affecting the original
- Use it to adapt community patterns to your specific domain

**Publishing Your Own Assets:**
- Create a collection (other than the Default Collection) with the cases or documents you want to share
- Navigate to **My Collections** → select the collection → click **Publish**
- Choose visibility (Public or Targeted)
- Add descriptive metadata to help others discover your work

**Rating and Reviewing:**
- Rate assets you've used (1-5 stars with optional review)
- Help the community identify high-quality patterns
- Cannot rate your own assets

For detailed marketplace architecture, product type specifications, and API documentation, see:
[**Intelligence Marketplace Architecture (docs/Architecture/MARKETPLACE_ARCHITECTURE.md)**](../Architecture/MARKETPLACE_ARCHITECTURE.md) · [Publisher republish guide →](../Marketplace/README.md)
