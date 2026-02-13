from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.ask import AskBackendError, AskExecutionError, AskService
from apps.api.experts import ExpertRankingBackendError, ExpertRankingError, ExpertRankingService
from apps.api.serializers import (
    AskQueryParamsSerializer,
    ExpertsQueryParamsSerializer,
    SearchQueryParamsSerializer,
)
from apps.api.services import SearchBackendError, SearchExecutionError, SearchService
from apps.common.demo_auth import get_session_name, get_session_role, resolve_clearance

logger = logging.getLogger(__name__)


class SearchBackendUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = "Search backend unavailable."
    default_code = "search_backend_unavailable"


class SearchView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request) -> Response:
        serializer = SearchQueryParamsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        user_role = get_session_role(request)
        clearance = resolve_clearance(
            requested_clearance=serializer.validated_data.get("clearance"),
            session_role=user_role,
        )
        client_id = request.headers.get("X-Client-Id") or get_session_name(request)

        service = SearchService()
        try:
            payload = service.search(
                query=serializer.validated_data["query"],
                clearance=clearance,
                page=serializer.validated_data["page"],
                endpoint="/api/search",
                client_id=client_id,
                user_role=user_role,
            )
        except SearchBackendError as exc:
            logger.exception("Search backend failed.")
            raise SearchBackendUnavailable() from exc
        except SearchExecutionError as exc:
            raise ValidationError(str(exc)) from exc

        return Response(payload, status=status.HTTP_200_OK)


class ExpertsView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request) -> Response:
        serializer = ExpertsQueryParamsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        user_role = get_session_role(request)
        clearance = resolve_clearance(
            requested_clearance=serializer.validated_data.get("clearance"),
            session_role=user_role,
        )
        client_id = request.headers.get("X-Client-Id") or get_session_name(request)

        service = ExpertRankingService()
        try:
            payload = service.rank(
                query=serializer.validated_data["query"],
                clearance=clearance,
                endpoint="/api/experts",
                client_id=client_id,
                user_role=user_role,
            )
        except ExpertRankingBackendError as exc:
            logger.exception("Experts ranking backend failed.")
            raise SearchBackendUnavailable() from exc
        except ExpertRankingError as exc:
            raise ValidationError(str(exc)) from exc

        return Response(payload, status=status.HTTP_200_OK)


class AskView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request: Request) -> Response:
        serializer = AskQueryParamsSerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        user_role = get_session_role(request)
        clearance = resolve_clearance(
            requested_clearance=serializer.validated_data.get("clearance"),
            session_role=user_role,
        )
        client_id = request.headers.get("X-Client-Id") or get_session_name(request)

        service = AskService()
        try:
            payload = service.ask(
                query=serializer.validated_data["query"],
                clearance=clearance,
                endpoint="/api/ask",
                client_id=client_id,
                user_role=user_role,
            )
        except AskBackendError as exc:
            logger.exception("Ask backend failed.")
            raise SearchBackendUnavailable() from exc
        except AskExecutionError as exc:
            raise ValidationError(str(exc)) from exc

        return Response(payload, status=status.HTTP_200_OK)
