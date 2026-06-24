from django.core.paginator import Paginator


def paginate(request, queryset, per_page=25):
    """Return a page_obj for the given queryset, preserving filter params."""
    paginator = Paginator(queryset, per_page)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)
    qs = request.GET.copy()
    if "page" in qs:
        del qs["page"]
    return page_obj, qs.urlencode()
