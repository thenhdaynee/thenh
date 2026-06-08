from django.contrib import admin
from .models import Post, Category, Comment, LiveScore

class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    readonly_fields = ('author_name', 'content', 'created_at')

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    prepopulated_fields = {'slug': ('name',)}
    list_display = ('name', 'icon', 'slug')

@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'categories_list', 'created_at', 'views_count', 'is_featured')
    list_filter = ('categories', 'is_featured', 'created_at')
    search_fields = ('title', 'content')
    filter_horizontal = ('categories',)
    list_editable = ('is_featured',)
    inlines = [CommentInline]

    def categories_list(self, obj):
        return " ".join([c.icon for c in obj.categories.all()])
    categories_list.short_description = "Danh mục"

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('author_name', 'post', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('author_name', 'content')


@admin.register(LiveScore)
class LiveScoreAdmin(admin.ModelAdmin):
    list_display = ('team_a', 'score_a', 'score_b', 'team_b', 'status', 'match_date')
    list_editable = ('score_a', 'score_b', 'status')
    list_filter = ('status', 'match_date')
    search_fields = ('team_a', 'team_b')
