from django.urls import path

from apps.ui.views import debug_data, demo_login, demo_logout, expert_profile, home

urlpatterns = [
    path("debug/data/", debug_data, name="ui_debug_data"),
    path("demo/login/", demo_login, name="demo_login"),
    path("demo/logout/", demo_logout, name="demo_logout"),
    path("", home, name="ui_home"),
    path("experts/<int:author_id>/", expert_profile, name="ui_expert_profile"),
]
