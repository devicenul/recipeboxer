"""Unit tests for SQLite SessionStore persistence layer."""

import pytest
from pathlib import Path

from recipeboxer.models.pipeline import PipelineState, ProgressMetrics, TokenUsage
from recipeboxer.models.recipe import RecipeSchema
from recipeboxer.storage.session_store import SessionStore, get_session_store


@pytest.fixture
def store(tmp_path: Path) -> SessionStore:
    """Provides a fresh SQLite SessionStore backed by a temporary file."""
    db_file = tmp_path / "test_sessions.db"
    store = SessionStore(db_path=db_file)
    yield store
    store.close()


@pytest.fixture
def memory_store() -> SessionStore:
    """Provides an in-memory SQLite SessionStore."""
    store = SessionStore(db_path=":memory:")
    yield store
    store.close()


class TestSessionStore:
    """Test suite covering SQLite SessionStore operations, serialization, and async methods."""

    def test_init_and_tables(self, store: SessionStore):
        """Test database and tables initialization."""
        assert store.list_sessions() == []
        assert store.list_recipes() == []

    def test_save_and_get_session(self, store: SessionStore):
        """Test saving and retrieving session records."""
        progress = ProgressMetrics(total_scans=10, fingerprinted=5, percent_complete=50.0)
        tokens = TokenUsage(prompt_tokens=100, total_tokens=150)

        store.save_session(
            session_id="sess_001",
            state=PipelineState.FINGERPRINTING,
            mode="parallel",
            folder_path="/scans/deck1",
            config={"max_concurrency": 4},
            progress=progress,
            token_usage=tokens,
            card_count=10,
            recipe_count=0,
        )

        # Get raw dict
        raw = store.get_session("sess_001")
        assert raw is not None
        assert raw["session_id"] == "sess_001"
        assert raw["state"] == "FINGERPRINTING"
        assert raw["folder_path"] == "/scans/deck1"
        assert raw["card_count"] == 10

        # Get summary
        summary = store.get_session_summary("sess_001")
        assert summary is not None
        assert summary.session_id == "sess_001"
        assert summary.status == "FINGERPRINTING"
        assert summary.card_count == 10

        # Get detail
        detail = store.get_session_detail("sess_001")
        assert detail is not None
        assert detail.session_id == "sess_001"
        assert detail.progress.total_scans == 10
        assert detail.token_usage.prompt_tokens == 100

    def test_update_existing_session(self, store: SessionStore):
        """Test updating an existing session preserves creation date and updates status."""
        store.save_session(
            session_id="sess_002",
            state=PipelineState.INGESTING,
            mode="parallel",
            folder_path="/scans/deck2",
        )

        detail_1 = store.get_session_detail("sess_002")
        assert detail_1 is not None
        created_at_1 = detail_1.created_at

        # Update state to COMPLETED
        store.save_session(
            session_id="sess_002",
            state=PipelineState.COMPLETED,
            mode="parallel",
            recipe_count=5,
        )

        detail_2 = store.get_session_detail("sess_002")
        assert detail_2 is not None
        assert detail_2.status == "COMPLETED"
        assert detail_2.created_at == created_at_1
        assert detail_2.recipe_count == 5

    def test_list_sessions_ordering(self, store: SessionStore):
        """Test listing sessions returns entries sorted by creation date."""
        store.save_session(
            session_id="sess_old",
            state=PipelineState.COMPLETED,
            created_at="2026-01-01T00:00:00Z",
        )
        store.save_session(
            session_id="sess_new",
            state=PipelineState.INGESTING,
            created_at="2026-02-01T00:00:00Z",
        )

        sessions = store.list_sessions()
        assert len(sessions) == 2
        assert sessions[0].session_id == "sess_new"
        assert sessions[1].session_id == "sess_old"

    def test_delete_session_cascades(self, store: SessionStore):
        """Test deleting a session removes cards, proposals, and recipes."""
        sid = "sess_to_delete"
        store.save_session(sid, state=PipelineState.COMPLETED)
        store.save_card(sid, card_id="card_1", image_path="/path/card1.jpg")
        store.save_proposals(sid, [{"proposal_id": "prop_1", "front_card_id": "card_1"}])

        recipe = RecipeSchema(title="Test Recipe", ingredients=["1 cup Flour"], directions=["Bake."])
        store.save_recipe(sid, recipe)

        assert store.get_session(sid) is not None
        assert len(store.get_cards(sid)) == 1
        assert len(store.get_proposals(sid)) == 1
        assert len(store.list_recipes(sid)) == 1

        # Delete session
        deleted = store.delete_session(sid)
        assert deleted is True

        assert store.get_session(sid) is None
        assert len(store.get_cards(sid)) == 0
        assert len(store.get_proposals(sid)) == 0
        assert len(store.list_recipes(sid)) == 0

    def test_card_operations(self, store: SessionStore):
        """Test card saving, retrieval, and path resolution."""
        sid = "sess_cards"
        store.save_session(sid, state=PipelineState.INGESTING)

        store.save_card(
            session_id=sid,
            card_id="scan_01.jpg",
            image_path="/scans/scan_01.jpg",
            sha256_hash="abc123hash",
            role="FRONT_ONLY",
            json_data={"format": "JPEG", "width": 800, "height": 600},
        )

        cards = store.get_cards(sid)
        assert len(cards) == 1
        assert cards[0]["card_id"] == "scan_01.jpg"
        assert cards[0]["image_path"] == "/scans/scan_01.jpg"
        assert cards[0]["sha256_hash"] == "abc123hash"
        assert cards[0]["metadata"]["width"] == 800

        # Path lookup
        assert store.get_card_path("scan_01.jpg", sid) == "/scans/scan_01.jpg"
        assert store.get_card_path("scan_01.jpg") == "/scans/scan_01.jpg"
        assert store.get_card_path("nonexistent") is None

    def test_recipe_crud_operations(self, store: SessionStore):
        """Test creating, reading, updating, listing, and deleting recipes."""
        sid = "sess_recipes"
        store.save_session(sid, state=PipelineState.COMPLETED)

        r1 = RecipeSchema(
            title="Pancakes",
            description="Fluffy breakfast pancakes",
            yield_servings="4 servings",
            prep_time="10 mins",
            cook_time="15 mins",
            total_time="25 mins",
            ingredients=["1 cup flour", "1 cup milk", "1 egg"],
            directions=["1. Mix ingredients.", "2. Cook on griddle."],
            tags=["Breakfast", "Quick"],
            card_ids=["card_front.jpg"],
        )

        r2 = RecipeSchema(
            title="Waffles",
            ingredients=["2 cups flour", "2 eggs"],
            directions=["1. Heat iron.", "2. Pour batter."],
            tags=["Breakfast"],
        )

        # Save single and batch
        store.save_recipe(sid, r1)
        store.save_recipe(sid, r2)

        # List recipes for session
        recipes = store.list_recipes(sid)
        assert len(recipes) == 2
        assert any(r.title == "Pancakes" for r in recipes)
        assert any(r.title == "Waffles" for r in recipes)

        # Get individual recipe
        fetched = store.get_recipe(r1.recipe_id, sid)
        assert fetched is not None
        assert fetched.title == "Pancakes"
        assert fetched.card_ids == ["card_front.jpg"]
        assert fetched.created_at is not None

        # Update recipe
        fetched.description = "Updated pancake description"
        store.save_recipe(sid, fetched)

        updated = store.get_recipe(r1.recipe_id, sid)
        assert updated is not None
        assert updated.description == "Updated pancake description"

        # Delete recipe
        deleted = store.delete_recipe(r2.recipe_id, sid)
        assert deleted is True
        assert len(store.list_recipes(sid)) == 1
        assert store.get_recipe(r2.recipe_id, sid) is None

    @pytest.mark.asyncio
    async def test_async_interface(self, store: SessionStore):
        """Test asynchronous non-blocking SessionStore methods."""
        sid = "sess_async"
        await store.asave_session(sid, state=PipelineState.COMPLETED)

        summary = await store.aget_session_summary(sid)
        assert summary is not None
        assert summary.session_id == sid

        recipe = RecipeSchema(title="Async Scones", ingredients=["Flour", "Butter"], directions=["Mix and bake."])
        await store.asave_recipe(sid, recipe)

        recipes = await store.alist_recipes(sid)
        assert len(recipes) == 1
        assert recipes[0].title == "Async Scones"

        fetched = await store.aget_recipe(recipe.recipe_id, sid)
        assert fetched is not None
        assert fetched.title == "Async Scones"

        deleted = await store.adelete_recipe(recipe.recipe_id, sid)
        assert deleted is True

        all_sessions = await store.alist_sessions()
        assert any(s.session_id == sid for s in all_sessions)

        sess_deleted = await store.adelete_session(sid)
        assert sess_deleted is True

    def test_global_singleton(self):
        """Test get_session_store returns a singleton instance."""
        s1 = get_session_store(":memory:")
        s2 = get_session_store()
        assert s1 is s2
