"""Infrastructure: the parts of the application that touch the outside world.

This layer owns SQLite, the file system and any import source. It deliberately
holds no scheduling rule: everything here either stores a value it was given or
reads one back unchanged. The domain decides what a visit is; this layer only
remembers that a decision was made.
"""
