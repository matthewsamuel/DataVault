"""Small helpers shared by several test files."""


def timeline_files(data):
    """The compact /api/timeline/scan rows as dicts with an absolute path."""
    root = data["root"].rstrip("/")
    columns = data["columns"]
    files = []
    for row in data["rows"]:
        item = dict(zip(columns, row))
        item["path"] = f"{root}/{item['path']}"
        files.append(item)
    return files
