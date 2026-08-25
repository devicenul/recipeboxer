"""RecipeBoxer FastAPI REST API layer."""

from recipeboxer.api.app import create_app
from recipeboxer.api.routes import router

__all__ = ["create_app", "router"]
