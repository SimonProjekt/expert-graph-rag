from django.urls import path

from apps.api.views import AskView, ExpertsView, SearchView

urlpatterns = [
    path("ask", AskView.as_view(), name="ask"),
    path("experts", ExpertsView.as_view(), name="experts"),
    path("search", SearchView.as_view(), name="search"),
]
