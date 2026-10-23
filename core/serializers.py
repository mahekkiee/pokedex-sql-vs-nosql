"""Input validation shared by both APIs.

Validation lives here (not in sql_api or mongo_api) because both backends must
accept exactly the same requests. These are plain DRF serializers: they check
the incoming data, they do not save anything.
"""
from rest_framework import serializers

from .constants import (
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MAX_TEAM_SIZE, ORDERING_CHOICES, TOP_STAT_CHOICES,
)


class PokemonListQuerySerializer(serializers.Serializer):
    """Query parameters for GET /pokemon/."""

    type = serializers.CharField(source="type_name", required=False, max_length=30)
    search = serializers.CharField(required=False, max_length=60)
    min_attack = serializers.IntegerField(required=False, min_value=0)
    ordering = serializers.ChoiceField(choices=ORDERING_CHOICES, required=False, default="id")
    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=MAX_PAGE_SIZE, default=DEFAULT_PAGE_SIZE
    )
    offset = serializers.IntegerField(required=False, min_value=0, default=0)


class TopStatQuerySerializer(serializers.Serializer):
    """Query parameters for GET /stats/top/."""

    stat = serializers.ChoiceField(choices=TOP_STAT_CHOICES, required=False, default="attack")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=100, default=10)


class TeamMemberInputSerializer(serializers.Serializer):
    pokemon = serializers.IntegerField(min_value=1, help_text="PokeAPI id, e.g. 25 for Pikachu")
    nickname = serializers.CharField(required=False, allow_blank=True, max_length=30, default="")


class TeamInputSerializer(serializers.Serializer):
    """Body for POST /teams/ and PUT /teams/<id>/."""

    name = serializers.CharField(max_length=50)
    members = TeamMemberInputSerializer(many=True, required=False)

    def validate_members(self, members):
        if len(members) > MAX_TEAM_SIZE:
            raise serializers.ValidationError(f"A team can have at most {MAX_TEAM_SIZE} Pokemon.")
        return members

    def validate(self, attrs):
        attrs.setdefault("members", [])
        return attrs
