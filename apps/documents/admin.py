from django.contrib import admin

from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    IngestionRun,
    Paper,
    PaperTopic,
    SearchAudit,
    Topic,
)


class AuthorshipInline(admin.TabularInline):
    model = Authorship
    extra = 0
    autocomplete_fields = ("author",)


class PaperTopicInline(admin.TabularInline):
    model = PaperTopic
    extra = 0
    autocomplete_fields = ("topic",)


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "external_id", "institution_name", "centrality_score")
    list_filter = ("institution_name",)
    search_fields = ("name", "external_id", "institution_name")
    ordering = ("name",)


@admin.register(Topic)
class TopicAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "external_id")
    search_fields = ("name", "external_id")
    ordering = ("name",)


@admin.register(Paper)
class PaperAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "published_date", "security_level", "doi", "external_id")
    list_filter = ("security_level", "published_date")
    search_fields = ("title", "abstract", "doi", "external_id")
    date_hierarchy = "published_date"
    inlines = (AuthorshipInline, PaperTopicInline)
    show_full_result_count = False


@admin.register(Authorship)
class AuthorshipAdmin(admin.ModelAdmin):
    list_display = ("id", "paper", "author", "author_order")
    list_filter = ("author_order",)
    search_fields = ("paper__title", "paper__external_id", "author__name", "author__external_id")
    list_select_related = ("paper", "author")


@admin.register(PaperTopic)
class PaperTopicAdmin(admin.ModelAdmin):
    list_display = ("id", "paper", "topic")
    search_fields = ("paper__title", "paper__external_id", "topic__name", "topic__external_id")
    list_select_related = ("paper", "topic")


@admin.register(Embedding)
class EmbeddingAdmin(admin.ModelAdmin):
    list_display = ("id", "paper", "chunk_id", "created_at")
    list_filter = ("created_at",)
    search_fields = ("paper__title", "paper__external_id", "text_chunk")
    list_select_related = ("paper",)
    readonly_fields = ("created_at",)
    show_full_result_count = False


@admin.register(SearchAudit)
class SearchAuditAdmin(admin.ModelAdmin):
    list_display = (
        "timestamp",
        "endpoint",
        "user_role",
        "clearance",
        "redacted_count",
        "client_id",
    )
    list_filter = ("user_role", "clearance", "endpoint", "timestamp")
    search_fields = ("endpoint", "query", "client_id")
    date_hierarchy = "timestamp"
    readonly_fields = (
        "timestamp",
        "endpoint",
        "query",
        "user_role",
        "clearance",
        "redacted_count",
        "client_id",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(IngestionRun)
class IngestionRunAdmin(admin.ModelAdmin):
    list_display = ("started_at", "finished_at", "status")
    list_filter = ("status", "started_at")
    search_fields = ("query", "error_message")
    date_hierarchy = "started_at"
    readonly_fields = ("query", "started_at", "finished_at", "counts", "status", "error_message")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
