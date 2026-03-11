from forum_read_routes import register_forum_read_routes
from forum_write_routes import register_forum_write_routes


def register_forum_routes(app, ctx):
    register_forum_read_routes(app, ctx)
    register_forum_write_routes(app, ctx)
