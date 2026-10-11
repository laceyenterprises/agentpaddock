def configure(template, label):
    selected = template.copy()
    selected["tags"] = template["tags"].copy()
    selected["tags"].append(label)
    return selected
