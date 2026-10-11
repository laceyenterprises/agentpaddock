import importlib


def resolve(spec):
    """Return the function a "module:function" spec names."""
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)
