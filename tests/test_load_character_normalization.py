from talemate.load import normalize_character_names


def test_normalize_character_names_repairs_persisted_rename_state():
    scene_data = {
        "character_data": {
            "Old Name": {
                "name": "New Name ",
            },
            "Inactive": {
                "name": "Inactive",
            },
        },
        "active_characters": ["Old Name", "Missing"],
        "world_state": {
            "characters": {"Old Name": {"emotion": "calm"}},
            "reinforce": [{"character": "Old Name", "question": "Mood?"}],
        },
    }

    mappings = normalize_character_names(scene_data)

    assert mappings == {"Old Name": "New Name"}
    assert list(scene_data["character_data"]) == ["New Name", "Inactive"]
    assert scene_data["character_data"]["New Name"]["name"] == "New Name"
    assert scene_data["active_characters"] == ["New Name"]
    assert "New Name" in scene_data["world_state"]["characters"]
    assert scene_data["world_state"]["reinforce"][0]["character"] == "New Name"
