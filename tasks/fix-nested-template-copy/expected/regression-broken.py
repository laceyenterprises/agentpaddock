def configure(template, label):
    selected = template.copy()
    selected["limit"] = 3
    selected["tags"] = template["tags"].copy()
    selected["tags"].append(label)
    return selected
