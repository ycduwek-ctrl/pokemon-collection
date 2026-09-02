import asyncio
import io
import json
import os
import unittest
from unittest.mock import patch

try:
    from fastapi import UploadFile
    from PIL import Image
    import main
except ModuleNotFoundError:
    UploadFile = None
    Image = None
    main = None


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        concept = {
            "title": "Wrong model title",
            "subtitle": "הרפתקה עם ברקים סגולים",
            "hp": 250,
            "type": "electric",
            "move1Name": "כדור חברות",
            "move1Damage": 90,
            "move1Text": "כל חבר מוסיף כוח למתקפה.",
            "move2Name": "סערת דמיון",
            "move2Damage": 180,
            "move2Text": "ברק מיוחד נשלח אל היריב.",
            "flavor": "קלף שנוצר מהרפתקה בלתי נשכחת.",
            "rarity": "אגדי",
        }
        return {"choices": [{"message": {"content": json.dumps(concept)}}]}


def portrait_upload():
    buffer = io.BytesIO()
    Image.new("RGB", (240, 320), "purple").save(buffer, format="JPEG")
    buffer.seek(0)
    return UploadFile(filename="portrait.jpg", file=buffer)


@unittest.skipIf(main is None, "backend dependencies are not installed")
class FantasyCardTests(unittest.TestCase):
    def test_clean_concept_clamps_values_and_preserves_requested_title(self):
        clean = main._clean_fantasy_concept(
            {"title": "ignored", "hp": 999, "type": "unknown", "rarity": "official"},
            "יוסי ופיקאצ׳ו ex",
        )
        self.assertEqual(clean["title"], "יוסי ופיקאצ׳ו ex")
        self.assertEqual(clean["hp"], 360)
        self.assertEqual(clean["type"], "psychic")
        self.assertEqual(clean["rarity"], "נדיר במיוחד")

    def test_endpoint_uses_approved_auth_and_returns_local_design_mode(self):
        with patch.object(main, "require_access") as require_access, patch.object(
            main.requests, "post", return_value=FakeResponse()
        ), patch.dict(os.environ, {"OPENROUTER_KEY": "test-key"}):
            result = asyncio.run(main.fantasy_card_concept(
                photo=portrait_upload(),
                prompt="אני מחזיק כדור ולידי חבר חשמלי",
                card_name="יוסי ופיקאצ׳ו ex",
                attempt=2,
                authorization="Bearer approved",
            ))
        require_access.assert_called_once_with("Bearer approved")
        self.assertEqual(result["concept"]["title"], "יוסי ופיקאצ׳ו ex")
        self.assertEqual(result["concept"]["type"], "electric")
        self.assertEqual(result["source"], "ai-text-vision")
        self.assertEqual(result["imageMode"], "local-fantasy-design")


if __name__ == "__main__":
    unittest.main()
