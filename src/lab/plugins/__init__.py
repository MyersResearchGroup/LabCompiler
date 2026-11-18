"""Adapters between external file formats or systems and Lab's own types.

Each plugin wraps core objects such as ``lab.plates.PlateDesign``; the core never depends on
a plugin. Plugins may require optional extras, which they import at module level.
"""
