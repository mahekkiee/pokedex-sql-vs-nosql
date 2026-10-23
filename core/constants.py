"""Values shared by both backends, so the two APIs stay identical."""

# The six base stats every Pokemon has, in PokeAPI order.
STAT_FIELDS = ("hp", "attack", "defense", "special_attack", "special_defense", "speed")

# Fields the list endpoint may be sorted by (prefix with "-" for descending).
SORTABLE_FIELDS = ("id", "name", "total_stats") + STAT_FIELDS

ORDERING_CHOICES = tuple(f for field in SORTABLE_FIELDS for f in (field, f"-{field}"))

# Stats the /stats/top/ endpoint accepts.
TOP_STAT_CHOICES = STAT_FIELDS + ("total_stats",)

MAX_TEAM_SIZE = 6
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
