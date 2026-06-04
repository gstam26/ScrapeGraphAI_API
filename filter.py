from models import PageDoc, ColumnSpec, RoutedPage


def filter_page(page: PageDoc, columns: list[ColumnSpec] | None = None) -> RoutedPage:
    """
    Route a page to extraction with cell relevance markers.
    
    MVP: Mark all requested columns as relevant for all pages.
    
    Future: Use embedding-based routing to mark only relevant columns,
    reducing unnecessary extraction calls.
    """
    
    # For MVP: all columns relevant for all pages
    relevant_columns = {col.name for col in (columns or [])}
    
    return RoutedPage(
        page=page,
        relevant_columns=relevant_columns,
    )
