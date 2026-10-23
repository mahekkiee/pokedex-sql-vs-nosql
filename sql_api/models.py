"""Relational (normalised) model for PostgreSQL.

Each fact is stored once and tables are linked with foreign keys:

    Type <--- PokemonType ---> Pokemon <--- PokemonAbility ---> Ability
                                  ^
                                  |
                Team <--- TeamMember

* A Pokemon has 1-2 types and 1-3 abilities, and a type or ability belongs to
  many Pokemon. Those many-to-many links live in their own "through" tables,
  which also store extra facts about the link (slot, is_hidden).
* The six base stats are always the same six numbers, so they are plain columns.
* A Team references Pokemon by foreign key; reading a team needs JOINs.
The schema is defined here and created by migrations (python manage.py migrate).
"""
from django.db import models
from django.db.models import F, Q


class Type(models.Model):
    name = models.CharField(max_length=30, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Ability(models.Model):
    name = models.CharField(max_length=60, unique=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "abilities"

    def __str__(self):
        return self.name


class Pokemon(models.Model):
    id = models.PositiveIntegerField(primary_key=True, help_text="Same id as PokeAPI")
    name = models.CharField(max_length=60, unique=True)
    height = models.PositiveIntegerField(help_text="Decimetres")
    weight = models.PositiveIntegerField(help_text="Hectograms")
    base_experience = models.PositiveIntegerField(null=True, blank=True)
    sprite_url = models.URLField(max_length=300, blank=True)

    hp = models.PositiveSmallIntegerField()
    attack = models.PositiveSmallIntegerField()
    defense = models.PositiveSmallIntegerField()
    special_attack = models.PositiveSmallIntegerField()
    special_defense = models.PositiveSmallIntegerField()
    speed = models.PositiveSmallIntegerField()
    total_stats = models.PositiveSmallIntegerField()

    types = models.ManyToManyField(Type, through="PokemonType", related_name="pokemon")
    abilities = models.ManyToManyField(Ability, through="PokemonAbility", related_name="pokemon")

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "pokemon"
        # Match the sorts the API runs: highest value first, id as tie-breaker.
        # mongo_api/schema.py creates the same two indexes in MongoDB.
        indexes = [
            models.Index(F("attack").desc(), F("id").asc(), name="pokemon_attack_desc_id"),
            models.Index(F("total_stats").desc(), F("id").asc(), name="pokemon_total_desc_id"),
        ]

    def __str__(self):
        return f"#{self.id} {self.name}"


class PokemonType(models.Model):
    pokemon = models.ForeignKey(Pokemon, on_delete=models.CASCADE, related_name="type_slots")
    type = models.ForeignKey(Type, on_delete=models.CASCADE, related_name="pokemon_slots")
    slot = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["pokemon_id", "slot"]
        constraints = [
            models.UniqueConstraint(fields=["pokemon", "slot"], name="unique_type_slot"),
            models.UniqueConstraint(fields=["pokemon", "type"], name="unique_pokemon_type"),
        ]


class PokemonAbility(models.Model):
    pokemon = models.ForeignKey(Pokemon, on_delete=models.CASCADE, related_name="ability_slots")
    ability = models.ForeignKey(Ability, on_delete=models.CASCADE, related_name="pokemon_slots")
    slot = models.PositiveSmallIntegerField()
    is_hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ["pokemon_id", "slot"]
        constraints = [
            models.UniqueConstraint(fields=["pokemon", "slot"], name="unique_ability_slot"),
        ]


class Team(models.Model):
    name = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return self.name


class TeamMember(models.Model):
    team = models.ForeignKey(Team, on_delete=models.CASCADE, related_name="members")
    # PROTECT: the database refuses to delete a Pokemon that is still in a team.
    pokemon = models.ForeignKey(Pokemon, on_delete=models.PROTECT, related_name="team_memberships")
    slot = models.PositiveSmallIntegerField()
    nickname = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["team_id", "slot"]
        constraints = [
            models.UniqueConstraint(fields=["team", "slot"], name="unique_team_slot"),
            models.CheckConstraint(condition=Q(slot__gte=1, slot__lte=6), name="team_slot_1_to_6"),
        ]
