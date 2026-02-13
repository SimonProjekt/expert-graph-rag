from __future__ import annotations

from rest_framework import serializers

from apps.documents.models import SecurityLevel


class QueryClearanceParamsSerializer(serializers.Serializer):
    query = serializers.CharField(max_length=2000, allow_blank=False, trim_whitespace=True)
    clearance = serializers.ChoiceField(choices=SecurityLevel.values, required=False)

    def validate_query(self, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise serializers.ValidationError("query cannot be empty.")
        return normalized


class SearchQueryParamsSerializer(QueryClearanceParamsSerializer):
    page = serializers.IntegerField(min_value=1, default=1)


class ExpertsQueryParamsSerializer(QueryClearanceParamsSerializer):
    pass


class AskQueryParamsSerializer(QueryClearanceParamsSerializer):
    pass
