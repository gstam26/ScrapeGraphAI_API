from models import PageDoc


def filter_page(page: PageDoc) -> PageDoc:
    """
    Week 1 version: passthrough.

    Later this can remove irrelevant content, navigation text,
    cookies, menus, duplicated footer text, etc.
    """
    return page