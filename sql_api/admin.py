"""Django admin: a free CRUD interface for the PostgreSQL data.

There is no equivalent for MongoDB inside Django, which is one of the
"ease of use" points in our comparison (MongoDB needs Compass or mongosh).
"""
from django.contrib import admin

from .models import Ability, Pokemon, PokemonAbility, PokemonType, Team, TeamMember, Type


class PokemonTypeInline(admin.TabularInline):
    model = PokemonType
    extra = 0


class PokemonAbilityInline(admin.TabularInline):
    model = PokemonAbility
    extra = 0


@admin.register(Pokemon)
class PokemonAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "hp", "attack", "defense", "speed", "total_stats")
    search_fields = ("name",)
    list_filter = ("types",)
    inlines = [PokemonTypeInline, PokemonAbilityInline]


class TeamMemberInline(admin.TabularInline):
    model = TeamMember
    extra = 0
    autocomplete_fields = ("pokemon",)


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "created_at")
    inlines = [TeamMemberInline]


admin.site.register(Type)
admin.site.register(Ability)
