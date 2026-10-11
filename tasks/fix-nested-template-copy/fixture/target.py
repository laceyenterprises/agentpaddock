def configure(template, label):
    selected = template.copy()
    selected["tags"].append(label)
    return selected
