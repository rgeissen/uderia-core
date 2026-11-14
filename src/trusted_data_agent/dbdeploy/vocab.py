"""
vocab — curated, ASCII-only vocabularies for the semantic demo-data generator.

Deliberately zero-dependency (no Faker): the lists are small, reviewable, and
the same on every machine, which is what makes a deploy reproducible.  Every
string is plain ASCII because the deployed columns are ``CHARACTER SET LATIN``.

The load-bearing structure is :data:`PLACES` — a place carries its own country
codes, currency, postal shape and phone prefix, so ONE draw per row yields a
city, state, country, postal code, phone and currency that agree with each
other (a row whose city is Munich, country is Japan and currency is BRL is the
kind of noise that makes demo data read as fake).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

FIRST_NAMES: List[str] = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David",
    "Elizabeth", "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas",
    "Sarah", "Charles", "Karen", "Christopher", "Lisa", "Daniel", "Nancy", "Matthew", "Betty",
    "Anthony", "Sandra", "Mark", "Margaret", "Donald", "Ashley", "Steven", "Kimberly", "Paul",
    "Emily", "Andrew", "Donna", "Joshua", "Michelle", "Kenneth", "Carol", "Kevin", "Amanda",
    "Brian", "Melissa", "George", "Deborah", "Timothy", "Stephanie", "Ronald", "Rebecca",
    "Edward", "Sharon", "Jason", "Laura", "Jeffrey", "Cynthia", "Ryan", "Dorothy", "Jacob",
    "Amy", "Gary", "Kathleen", "Nicholas", "Angela", "Eric", "Shirley", "Jonathan", "Brenda",
    "Stephen", "Emma", "Larry", "Anna", "Justin", "Pamela", "Scott", "Nicole", "Brandon",
    "Samantha", "Benjamin", "Katherine", "Samuel", "Christine", "Gregory", "Helen", "Alexander",
    "Debra", "Patrick", "Rachel", "Frank", "Carolyn", "Raymond", "Janet", "Jack", "Maria",
    "Dennis", "Olivia", "Jerry", "Heather", "Tyler", "Diane", "Aaron", "Julie", "Jose", "Joyce",
    "Adam", "Victoria", "Nathan", "Ruth", "Henry", "Virginia", "Zachary", "Lauren", "Douglas",
    "Kelly", "Peter", "Christina", "Kyle", "Joan", "Noah", "Evelyn", "Ethan", "Judith", "Jeremy",
    "Andrea", "Walter", "Hannah", "Christian", "Megan", "Keith", "Cheryl", "Roger", "Jacqueline",
    "Terry", "Martha", "Austin", "Madison", "Sean", "Teresa", "Gerald", "Gloria", "Carl", "Sara",
    "Harold", "Janice", "Dylan", "Ann", "Arthur", "Kathryn", "Lawrence", "Abigail", "Jordan",
    "Sophia", "Jesse", "Frances", "Bryan", "Jean", "Billy", "Alice", "Bruce", "Judy", "Gabriel",
    "Isabella", "Joe", "Julia", "Logan", "Grace", "Alan", "Amber", "Juan", "Denise", "Albert",
    "Danielle", "Willie", "Marilyn", "Elijah", "Beverly", "Wayne", "Charlotte", "Randy", "Natalie",
    "Vincent", "Theresa", "Mason", "Diana", "Roy", "Brittany", "Ralph", "Doris", "Bobby",
    "Kayla", "Russell", "Alexis", "Bradley", "Lori", "Lukas", "Anja", "Felix", "Lena", "Jonas",
    "Sofia", "Mateo", "Chloe", "Hugo", "Elena", "Luca", "Mia", "Ravi", "Priya", "Arjun", "Ananya",
    "Wei", "Mei", "Hiroshi", "Yuki", "Omar", "Layla", "Carlos", "Valentina", "Diego", "Camila",
]

LAST_NAMES: List[str] = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez",
    "Martinez", "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor",
    "Moore", "Jackson", "Martin", "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez",
    "Clark", "Ramirez", "Lewis", "Robinson", "Walker", "Young", "Allen", "King", "Wright",
    "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams", "Nelson", "Baker", "Hall",
    "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Gomez", "Phillips", "Evans",
    "Turner", "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes", "Stewart", "Morris",
    "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Peterson",
    "Bailey", "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson", "Watson",
    "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz", "Hughes", "Price",
    "Alvarez", "Castillo", "Sanders", "Patel", "Myers", "Long", "Ross", "Foster", "Jimenez",
    "Powell", "Jenkins", "Perry", "Russell", "Sullivan", "Bell", "Coleman", "Butler", "Henderson",
    "Barnes", "Gonzales", "Fisher", "Vasquez", "Simmons", "Romero", "Jordan", "Patterson",
    "Alexander", "Hamilton", "Graham", "Reynolds", "Griffin", "Wallace", "Moreno", "West", "Cole",
    "Hayes", "Bryant", "Herrera", "Gibson", "Ellis", "Tran", "Medina", "Aguilar", "Stevens",
    "Murray", "Ford", "Castro", "Marshall", "Owens", "Harrison", "Fernandez", "McDonald", "Woods",
    "Washington", "Kennedy", "Wells", "Vargas", "Henry", "Chen", "Freeman", "Webb", "Tucker",
    "Guzman", "Burns", "Crawford", "Olson", "Simpson", "Porter", "Hunter", "Gordon", "Mendez",
    "Silva", "Shaw", "Snyder", "Mason", "Dixon", "Munoz", "Hunt", "Hicks", "Holmes", "Palmer",
    "Wagner", "Black", "Robertson", "Boyd", "Rose", "Stone", "Salazar", "Fox", "Warren", "Mills",
    "Meyer", "Rice", "Schmidt", "Garza", "Daniels", "Ferguson", "Nichols", "Stephens", "Soto",
    "Weaver", "Ryan", "Gardner", "Payne", "Grant", "Dunn", "Kelley", "Spencer", "Hawkins",
    "Arnold", "Pierce", "Vazquez", "Hansen", "Peters", "Santos", "Hart", "Bradley", "Knight",
    "Elliott", "Cunningham", "Duncan", "Armstrong", "Hudson", "Carroll", "Lane", "Riley",
    "Andrews", "Alvarado", "Ray", "Delgado", "Berry", "Perkins", "Hoffman", "Johnston",
    "Matthews", "Pena", "Richards", "Contreras", "Willis", "Carpenter", "Lawrence", "Sandoval",
    "Guerrero", "George", "Chapman", "Rios", "Estrada", "Ortega", "Watkins", "Greene", "Nunez",
    "Wheeler", "Valdez", "Harper", "Burke", "Larson", "Santiago", "Maldonado", "Morrison",
    "Franklin", "Carlson", "Austin", "Dominguez", "Carr", "Lawson", "Jacobs", "OBrien", "Lynch",
    "Singh", "Vega", "Bishop", "Montgomery", "Oliver", "Jensen", "Harvey", "Williamson", "Gilbert",
    "Dean", "Sims", "Espinoza", "Howell", "Li", "Wong", "Reid", "Hanson", "Le", "McCoy", "Garrett",
    "Burton", "Fuller", "Wang", "Weber", "Welch", "Rojas", "Lucas", "Marquez", "Fields", "Park",
    "Yang", "Little", "Banks", "Padilla", "Day", "Walsh", "Bowman", "Schultz", "Luna", "Fowler",
    "Mejia", "Becker", "Schneider", "Fischer", "Hoffmann", "Koch", "Bauer", "Richter", "Klein",
    "Wolf", "Schroeder", "Neumann", "Zimmermann", "Braun", "Krueger", "Hartmann", "Lange",
    "Werner", "Krause", "Lehmann", "Huber", "Mayer", "Rossi", "Russo", "Ferrari", "Esposito",
    "Bianchi", "Romano", "Colombo", "Ricci", "Marino", "Greco", "Dubois", "Bernard", "Moreau",
    "Laurent", "Lefebvre", "Roux", "Fournier", "Girard", "Sato", "Suzuki", "Takahashi", "Tanaka",
    "Watanabe", "Ito", "Yamamoto", "Nakamura", "Kobayashi", "Kato", "Sharma", "Gupta", "Kumar",
    "Mehta", "Iyer", "Reddy", "Nair", "Chaudhary", "Verma", "Malhotra",
]

# (city, region/state code, region name, country name, alpha-2, alpha-3, currency,
#  postal-shape, phone-prefix, timezone)
Place = Tuple[str, str, str, str, str, str, str, str, str, str]
PLACES: List[Place] = [
    ("New York", "NY", "New York", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Los Angeles", "CA", "California", "United States", "US", "USA", "USD", "#####", "+1", "America/Los_Angeles"),
    ("Chicago", "IL", "Illinois", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("Houston", "TX", "Texas", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("Phoenix", "AZ", "Arizona", "United States", "US", "USA", "USD", "#####", "+1", "America/Phoenix"),
    ("Philadelphia", "PA", "Pennsylvania", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("San Antonio", "TX", "Texas", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("San Diego", "CA", "California", "United States", "US", "USA", "USD", "#####", "+1", "America/Los_Angeles"),
    ("Dallas", "TX", "Texas", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("San Jose", "CA", "California", "United States", "US", "USA", "USD", "#####", "+1", "America/Los_Angeles"),
    ("Austin", "TX", "Texas", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("Jacksonville", "FL", "Florida", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Columbus", "OH", "Ohio", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Charlotte", "NC", "North Carolina", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Seattle", "WA", "Washington", "United States", "US", "USA", "USD", "#####", "+1", "America/Los_Angeles"),
    ("Denver", "CO", "Colorado", "United States", "US", "USA", "USD", "#####", "+1", "America/Denver"),
    ("Boston", "MA", "Massachusetts", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Atlanta", "GA", "Georgia", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Miami", "FL", "Florida", "United States", "US", "USA", "USD", "#####", "+1", "America/New_York"),
    ("Minneapolis", "MN", "Minnesota", "United States", "US", "USA", "USD", "#####", "+1", "America/Chicago"),
    ("San Francisco", "CA", "California", "United States", "US", "USA", "USD", "#####", "+1", "America/Los_Angeles"),
    ("Toronto", "ON", "Ontario", "Canada", "CA", "CAN", "CAD", "A#A #A#", "+1", "America/Toronto"),
    ("Vancouver", "BC", "British Columbia", "Canada", "CA", "CAN", "CAD", "A#A #A#", "+1", "America/Vancouver"),
    ("Montreal", "QC", "Quebec", "Canada", "CA", "CAN", "CAD", "A#A #A#", "+1", "America/Toronto"),
    ("London", "ENG", "England", "United Kingdom", "GB", "GBR", "GBP", "AA# #AA", "+44", "Europe/London"),
    ("Manchester", "ENG", "England", "United Kingdom", "GB", "GBR", "GBP", "AA# #AA", "+44", "Europe/London"),
    ("Edinburgh", "SCT", "Scotland", "United Kingdom", "GB", "GBR", "GBP", "AA# #AA", "+44", "Europe/London"),
    ("Dublin", "L", "Leinster", "Ireland", "IE", "IRL", "EUR", "A## AA##", "+353", "Europe/Dublin"),
    ("Berlin", "BE", "Berlin", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Munich", "BY", "Bavaria", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Hamburg", "HH", "Hamburg", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Frankfurt", "HE", "Hesse", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Cologne", "NW", "North Rhine-Westphalia", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Stuttgart", "BW", "Baden-Wuerttemberg", "Germany", "DE", "DEU", "EUR", "#####", "+49", "Europe/Berlin"),
    ("Vienna", "9", "Vienna", "Austria", "AT", "AUT", "EUR", "####", "+43", "Europe/Vienna"),
    ("Zurich", "ZH", "Zurich", "Switzerland", "CH", "CHE", "CHF", "####", "+41", "Europe/Zurich"),
    ("Geneva", "GE", "Geneva", "Switzerland", "CH", "CHE", "CHF", "####", "+41", "Europe/Zurich"),
    ("Paris", "IDF", "Ile-de-France", "France", "FR", "FRA", "EUR", "#####", "+33", "Europe/Paris"),
    ("Lyon", "ARA", "Auvergne-Rhone-Alpes", "France", "FR", "FRA", "EUR", "#####", "+33", "Europe/Paris"),
    ("Marseille", "PAC", "Provence-Alpes-Cote d'Azur", "France", "FR", "FRA", "EUR", "#####", "+33", "Europe/Paris"),
    ("Amsterdam", "NH", "North Holland", "Netherlands", "NL", "NLD", "EUR", "#### AA", "+31", "Europe/Amsterdam"),
    ("Rotterdam", "ZH", "South Holland", "Netherlands", "NL", "NLD", "EUR", "#### AA", "+31", "Europe/Amsterdam"),
    ("Brussels", "BRU", "Brussels", "Belgium", "BE", "BEL", "EUR", "####", "+32", "Europe/Brussels"),
    ("Madrid", "MD", "Madrid", "Spain", "ES", "ESP", "EUR", "#####", "+34", "Europe/Madrid"),
    ("Barcelona", "CT", "Catalonia", "Spain", "ES", "ESP", "EUR", "#####", "+34", "Europe/Madrid"),
    ("Lisbon", "11", "Lisbon", "Portugal", "PT", "PRT", "EUR", "####-###", "+351", "Europe/Lisbon"),
    ("Milan", "LO", "Lombardy", "Italy", "IT", "ITA", "EUR", "#####", "+39", "Europe/Rome"),
    ("Rome", "LA", "Lazio", "Italy", "IT", "ITA", "EUR", "#####", "+39", "Europe/Rome"),
    ("Turin", "PI", "Piedmont", "Italy", "IT", "ITA", "EUR", "#####", "+39", "Europe/Rome"),
    ("Stockholm", "AB", "Stockholm", "Sweden", "SE", "SWE", "SEK", "### ##", "+46", "Europe/Stockholm"),
    ("Copenhagen", "84", "Capital Region", "Denmark", "DK", "DNK", "DKK", "####", "+45", "Europe/Copenhagen"),
    ("Oslo", "03", "Oslo", "Norway", "NO", "NOR", "NOK", "####", "+47", "Europe/Oslo"),
    ("Helsinki", "18", "Uusimaa", "Finland", "FI", "FIN", "EUR", "#####", "+358", "Europe/Helsinki"),
    ("Warsaw", "MZ", "Masovia", "Poland", "PL", "POL", "PLN", "##-###", "+48", "Europe/Warsaw"),
    ("Prague", "10", "Prague", "Czech Republic", "CZ", "CZE", "CZK", "### ##", "+420", "Europe/Prague"),
    ("Budapest", "BU", "Budapest", "Hungary", "HU", "HUN", "HUF", "####", "+36", "Europe/Budapest"),
    ("Athens", "I", "Attica", "Greece", "GR", "GRC", "EUR", "### ##", "+30", "Europe/Athens"),
    ("Istanbul", "34", "Istanbul", "Turkey", "TR", "TUR", "TRY", "#####", "+90", "Europe/Istanbul"),
    ("Dubai", "DU", "Dubai", "United Arab Emirates", "AE", "ARE", "AED", "", "+971", "Asia/Dubai"),
    ("Riyadh", "01", "Riyadh", "Saudi Arabia", "SA", "SAU", "SAR", "#####", "+966", "Asia/Riyadh"),
    ("Tel Aviv", "TA", "Tel Aviv", "Israel", "IL", "ISR", "ILS", "#######", "+972", "Asia/Jerusalem"),
    ("Johannesburg", "GP", "Gauteng", "South Africa", "ZA", "ZAF", "ZAR", "####", "+27", "Africa/Johannesburg"),
    ("Cape Town", "WC", "Western Cape", "South Africa", "ZA", "ZAF", "ZAR", "####", "+27", "Africa/Johannesburg"),
    ("Cairo", "C", "Cairo", "Egypt", "EG", "EGY", "EGP", "#####", "+20", "Africa/Cairo"),
    ("Lagos", "LA", "Lagos", "Nigeria", "NG", "NGA", "NGN", "######", "+234", "Africa/Lagos"),
    ("Nairobi", "30", "Nairobi", "Kenya", "KE", "KEN", "KES", "#####", "+254", "Africa/Nairobi"),
    ("Mumbai", "MH", "Maharashtra", "India", "IN", "IND", "INR", "######", "+91", "Asia/Kolkata"),
    ("Delhi", "DL", "Delhi", "India", "IN", "IND", "INR", "######", "+91", "Asia/Kolkata"),
    ("Bangalore", "KA", "Karnataka", "India", "IN", "IND", "INR", "######", "+91", "Asia/Kolkata"),
    ("Hyderabad", "TG", "Telangana", "India", "IN", "IND", "INR", "######", "+91", "Asia/Kolkata"),
    ("Chennai", "TN", "Tamil Nadu", "India", "IN", "IND", "INR", "######", "+91", "Asia/Kolkata"),
    ("Singapore", "01", "Central", "Singapore", "SG", "SGP", "SGD", "######", "+65", "Asia/Singapore"),
    ("Kuala Lumpur", "14", "Kuala Lumpur", "Malaysia", "MY", "MYS", "MYR", "#####", "+60", "Asia/Kuala_Lumpur"),
    ("Bangkok", "10", "Bangkok", "Thailand", "TH", "THA", "THB", "#####", "+66", "Asia/Bangkok"),
    ("Jakarta", "JK", "Jakarta", "Indonesia", "ID", "IDN", "IDR", "#####", "+62", "Asia/Jakarta"),
    ("Manila", "00", "Metro Manila", "Philippines", "PH", "PHL", "PHP", "####", "+63", "Asia/Manila"),
    ("Hong Kong", "HK", "Hong Kong", "Hong Kong", "HK", "HKG", "HKD", "", "+852", "Asia/Hong_Kong"),
    ("Shanghai", "SH", "Shanghai", "China", "CN", "CHN", "CNY", "######", "+86", "Asia/Shanghai"),
    ("Beijing", "BJ", "Beijing", "China", "CN", "CHN", "CNY", "######", "+86", "Asia/Shanghai"),
    ("Shenzhen", "GD", "Guangdong", "China", "CN", "CHN", "CNY", "######", "+86", "Asia/Shanghai"),
    ("Taipei", "TPE", "Taipei", "Taiwan", "TW", "TWN", "TWD", "###", "+886", "Asia/Taipei"),
    ("Seoul", "11", "Seoul", "South Korea", "KR", "KOR", "KRW", "#####", "+82", "Asia/Seoul"),
    ("Tokyo", "13", "Tokyo", "Japan", "JP", "JPN", "JPY", "###-####", "+81", "Asia/Tokyo"),
    ("Osaka", "27", "Osaka", "Japan", "JP", "JPN", "JPY", "###-####", "+81", "Asia/Tokyo"),
    ("Sydney", "NSW", "New South Wales", "Australia", "AU", "AUS", "AUD", "####", "+61", "Australia/Sydney"),
    ("Melbourne", "VIC", "Victoria", "Australia", "AU", "AUS", "AUD", "####", "+61", "Australia/Melbourne"),
    ("Brisbane", "QLD", "Queensland", "Australia", "AU", "AUS", "AUD", "####", "+61", "Australia/Brisbane"),
    ("Auckland", "AUK", "Auckland", "New Zealand", "NZ", "NZL", "NZD", "####", "+64", "Pacific/Auckland"),
    ("Mexico City", "CMX", "Mexico City", "Mexico", "MX", "MEX", "MXN", "#####", "+52", "America/Mexico_City"),
    ("Guadalajara", "JAL", "Jalisco", "Mexico", "MX", "MEX", "MXN", "#####", "+52", "America/Mexico_City"),
    ("Sao Paulo", "SP", "Sao Paulo", "Brazil", "BR", "BRA", "BRL", "#####-###", "+55", "America/Sao_Paulo"),
    ("Rio de Janeiro", "RJ", "Rio de Janeiro", "Brazil", "BR", "BRA", "BRL", "#####-###", "+55", "America/Sao_Paulo"),
    ("Buenos Aires", "C", "Buenos Aires", "Argentina", "AR", "ARG", "ARS", "A####AAA", "+54", "America/Argentina/Buenos_Aires"),
    ("Santiago", "RM", "Santiago Metropolitan", "Chile", "CL", "CHL", "CLP", "#######", "+56", "America/Santiago"),
    ("Bogota", "DC", "Bogota", "Colombia", "CO", "COL", "COP", "######", "+57", "America/Bogota"),
    ("Lima", "LMA", "Lima", "Peru", "PE", "PER", "PEN", "#####", "+51", "America/Lima"),
]

# Weight the draw toward the first entries (US-heavy, then EU) — most demo
# audiences expect a home-market skew, and the skew is what makes GROUP BY
# country produce a readable ranking rather than a flat histogram.
PLACE_WEIGHT_EXPONENT = 1.8

CURRENCIES: List[str] = ["USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD", "SEK", "NOK", "DKK",
                         "SGD", "HKD", "INR", "CNY", "BRL", "MXN", "ZAR", "AED", "PLN", "KRW"]
LANGUAGES: List[str] = ["en", "de", "fr", "es", "it", "pt", "nl", "sv", "da", "no", "fi", "pl",
                        "ja", "zh", "ko", "hi", "ar", "tr", "el", "cs"]
LANGUAGE_TAGS: List[str] = ["en-US", "en-GB", "de-DE", "fr-FR", "es-ES", "it-IT", "pt-BR", "nl-NL",
                            "ja-JP", "zh-CN", "ko-KR", "hi-IN", "es-MX", "de-AT", "fr-CA"]

STREETS: List[str] = ["Main Street", "Oak Avenue", "Maple Drive", "Cedar Lane", "Park Road",
                      "Elm Street", "Washington Boulevard", "Lake View Drive", "Hill Street",
                      "River Road", "Church Street", "High Street", "Station Road", "Market Square",
                      "King Street", "Queen Street", "Mill Lane", "Bridge Road", "Broadway",
                      "Union Street", "Industrial Way", "Harbor Drive", "Sunset Boulevard",
                      "Pine Street", "Willow Court", "Ridge Road", "Forest Avenue", "Garden Lane",
                      "Commerce Street", "Technology Drive"]

COMPANY_STEMS: List[str] = ["Northwind", "Contoso", "Globex", "Initech", "Vandelay", "Acme",
                            "Umbrella", "Stark", "Wayne", "Hooli", "Pied Piper", "Aperture",
                            "Cyberdyne", "Tyrell", "Wonka", "Oscorp", "Massive Dynamic", "Sirius",
                            "Blue Sun", "Veridian", "Dunder Mifflin", "Prestige", "Cardinal",
                            "Meridian", "Summit", "Pinnacle", "Horizon", "Atlas", "Vertex",
                            "Zenith", "Keystone", "Beacon", "Harbor", "Evergreen", "Silverline",
                            "Ironwood", "Redwood", "Granite", "Sterling", "Crestview", "Lakeside",
                            "Northgate", "Southbridge", "Eastfield", "Westbrook", "Highland",
                            "Riverbend", "Oakridge", "Brightwater", "Clearpath"]
COMPANY_SUFFIXES: List[str] = ["Inc", "LLC", "Ltd", "GmbH", "AG", "SA", "Corp", "Group",
                               "Holdings", "Partners", "Industries", "Solutions", "Systems",
                               "Technologies", "Services", "Logistics", "Capital", "Trading"]

PRODUCT_ADJECTIVES: List[str] = ["Premium", "Standard", "Classic", "Essential", "Advanced",
                                 "Elite", "Basic", "Pro", "Ultra", "Compact", "Deluxe", "Prime",
                                 "Select", "Signature", "Core", "Flex", "Smart", "Rapid",
                                 "Secure", "Eco"]
PRODUCT_NOUNS: List[str] = ["Plan", "Package", "Bundle", "Series", "Edition", "Kit", "Service",
                            "Suite", "Line", "Model", "Option", "Tier", "Program", "Offer",
                            "Solution"]

STATUS_WORDS: List[str] = ["active", "pending", "approved", "closed", "inactive", "suspended",
                           "completed", "cancelled", "rejected", "draft"]
TYPE_WORDS: List[str] = ["standard", "premium", "basic", "enterprise", "retail", "corporate",
                         "internal", "external", "primary", "secondary"]
CATEGORY_WORDS: List[str] = ["general", "operations", "finance", "sales", "marketing", "service",
                             "compliance", "risk", "technology", "logistics"]
CHANNEL_WORDS: List[str] = ["online", "mobile", "branch", "call_center", "partner", "email",
                            "in_store", "api"]
METHOD_WORDS: List[str] = ["online", "manual", "automatic", "batch", "scheduled", "on_demand"]
PRIORITY_WORDS: List[str] = ["low", "medium", "high", "critical"]
TIER_WORDS: List[str] = ["bronze", "silver", "gold", "platinum"]
LEVEL_WORDS: List[str] = ["level_1", "level_2", "level_3", "level_4"]
SEGMENT_WORDS: List[str] = ["retail", "small_business", "commercial", "enterprise", "public_sector"]
REGION_WORDS: List[str] = ["North America", "EMEA", "APAC", "LATAM"]
FREQUENCY_WORDS: List[str] = ["daily", "weekly", "monthly", "quarterly", "annual"]
UNIT_WORDS: List[str] = ["each", "kg", "litre", "hour", "unit", "box", "pallet", "metre"]
SOURCE_SYSTEMS: List[str] = ["CORE", "CRM", "ERP", "LEGACY", "WEB", "MOBILE", "BATCH", "API"]
TIMEZONES: List[str] = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles",
                        "Europe/London", "Europe/Berlin", "Europe/Paris", "Asia/Singapore",
                        "Asia/Tokyo", "Australia/Sydney"]
DEPARTMENTS: List[str] = ["Finance", "Operations", "Sales", "Marketing", "Human Resources",
                          "Information Technology", "Legal", "Compliance", "Customer Service",
                          "Procurement", "Research and Development", "Logistics"]
JOB_TITLES: List[str] = ["Analyst", "Senior Analyst", "Manager", "Senior Manager", "Director",
                         "Associate", "Specialist", "Consultant", "Engineer", "Coordinator",
                         "Vice President", "Administrator", "Supervisor", "Advisor", "Lead"]
EMAIL_DOMAINS: List[str] = ["example.com", "example.org", "mail.example.com", "corp.example.net",
                            "example.co", "demo.example.com"]

SENTENCES: List[str] = [
    "Reviewed and approved in line with the standard operating procedure.",
    "Pending confirmation from the counterparty; follow-up scheduled.",
    "Escalated to the regional team for further assessment.",
    "Documentation received and verified against the source record.",
    "Adjustment applied after reconciliation of the period totals.",
    "Customer requested a callback to discuss the available options.",
    "No exceptions noted during the routine quality review.",
    "Awaiting supporting evidence before the case can be closed.",
    "Terms renegotiated at renewal; see the attached amendment.",
    "Flagged for manual review due to an unusual activity pattern.",
    "Transferred from the legacy system during the migration window.",
    "Routine maintenance completed with no impact on service levels.",
    "Priority raised at the request of the account owner.",
    "Data corrected following a validation report from the source system.",
    "Provisional entry; final figures to be confirmed at month end.",
    "Service level met; no further action required.",
    "Compliance check passed with minor observations recorded.",
    "Delivery rescheduled at the customer's request.",
    "Duplicate record merged; original reference retained for audit.",
    "Closed after successful resolution and customer confirmation.",
]

WORDS: List[str] = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel",
                    "india", "juliet", "kilo", "lima", "mike", "november", "oscar", "papa",
                    "quebec", "romeo", "sierra", "tango", "uniform", "victor", "whiskey",
                    "xray", "yankee", "zulu"]

# One place per country, in PLACES order — what a REFERENCE table (countries,
# currencies, jurisdictions) walks sequentially so its rows are distinct.
_seen: set = set()
COUNTRY_PLACES: List[Place] = [p for p in PLACES if not (p[5] in _seen or _seen.add(p[5]))]
del _seen

COUNTRY_ALPHA3: List[str] = sorted({p[5] for p in PLACES})
COUNTRY_ALPHA2: List[str] = sorted({p[4] for p in PLACES})
COUNTRY_NAMES: List[str] = sorted({p[3] for p in PLACES})

VOCAB_BY_TOKEN: Dict[str, List[str]] = {
    "status": STATUS_WORDS, "state": STATUS_WORDS, "stage": STATUS_WORDS,
    "type": TYPE_WORDS, "kind": TYPE_WORDS, "class": TYPE_WORDS, "classification": TYPE_WORDS,
    "category": CATEGORY_WORDS, "group": CATEGORY_WORDS, "family": CATEGORY_WORDS,
    "channel": CHANNEL_WORDS, "method": METHOD_WORDS, "mode": METHOD_WORDS,
    "priority": PRIORITY_WORDS, "severity": PRIORITY_WORDS, "urgency": PRIORITY_WORDS,
    "tier": TIER_WORDS, "level": LEVEL_WORDS, "grade": TIER_WORDS,
    "segment": SEGMENT_WORDS, "region": REGION_WORDS, "frequency": FREQUENCY_WORDS,
    "unit": UNIT_WORDS, "uom": UNIT_WORDS, "system": SOURCE_SYSTEMS, "source": SOURCE_SYSTEMS,
    "timezone": TIMEZONES, "department": DEPARTMENTS, "title": JOB_TITLES, "role": JOB_TITLES,
    "language": LANGUAGES, "locale": LANGUAGE_TAGS, "currency": CURRENCIES,
}
