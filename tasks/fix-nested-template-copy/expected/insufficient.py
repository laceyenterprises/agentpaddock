def configure(template, label):
    selected = dict(template)
    selected["tags"].append(label)
    return selected
