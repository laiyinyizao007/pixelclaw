"""
Step-1V (StepFun) Strategy

Uses StepFun's Step-1V vision-language model via API for screen understanding.
Level 1 in the fallback hierarchy.
"""

import base64
import json
import time
from io import BytesIO
from typing import Any, Dict, Optional

import httpx
from PIL import Image

from .base import Action, ActionType, BaseStrategy, StrategyLevel, StrategyResult


class Step1VStrategy(BaseStrategy):
    """
    Strategy using StepFun's Step-1V vision-language model.

    API Documentation: https://platform.stepfun.com/docs/overview/consumption
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.api_key = self.config.get("api_key", "")
        self.base_url = self.config.get("base_url", "https://api.stepfun.com/v1")
        self.model = self.config.get("model", "step-1v")
        self.timeout = self.config.get("timeout", 30)
        self.max_retries = self.config.get("max_retries", 3)
        self.retry_delay = self.config.get("retry_delay", 2.0)

    def get_level(self) -> StrategyLevel:
        return StrategyLevel.STEP1V

    def is_available(self) -> bool:
        """Check if API key is configured."""
        return bool(self.api_key) and len(self.api_key) > 20

    def _encode_image(self, image: Image.Image) -> str:
        """Convert PIL Image to base64 string."""
        buffer = BytesIO()
        # Convert to RGB if necessary (handles RGBA)
        if image.mode != "RGB":
            image = image.convert("RGB")
        image.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _build_prompt(self, goal: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Build the system prompt for Step-1V."""
        system_prompt = """You are a mobile device automation assistant. Analyze the provided screenshot and determine the next action to achieve the user's goal.

Available actions:
- TAP(x, y): Tap at screen coordinates (x, y)
- SWIPE(x1, y1, x2, y2): Swipe from (x1, y1) to (x2, y2)
- LONG_PRESS(x, y, duration_ms): Long press at (x, y) for duration
- TYPE(text): Type the specified text
- BACK(): Press the back button
- HOME(): Press the home button
- RECENT(): Show recent apps
- WAIT(seconds): Wait for the specified seconds
- COMPLETE(): Mark the task as completed

Respond ONLY with a JSON object in this exact format:
{
  "action": "TAP",
  "params": {"x": 500, "y": 800},
  "reasoning": "Click the login button to proceed",
  "confidence": 0.95
}

Important:
1. Coordinates should be within the screen bounds (typically 1080x2400 for modern phones)
2. Confidence should be between 0.0 and 1.0
3. Always provide clear reasoning for your action
4. If the screen is loading or unclear, use WAIT(2) to wait
5. If the task is complete, use COMPLETE()"""

        # Add context if available
        if context:
            history = context.get("action_history", [])
            if history:
                system_prompt += f"\n\nPrevious actions:\n"
                for i, act in enumerate(history[-5:], 1):  # Last 5 actions
                    system_prompt += f"{i}. {act}\n"

        user_prompt = f"Goal: {goal}\n\nAnalyze the screenshot and provide the next action."

        return system_prompt, user_prompt

    def _parse_response(self, response_text: str) -> Optional[Action]:
        """Parse the API response into an Action."""
        try:
            # Extract JSON from response (handle markdown code blocks)
            json_str = response_text
            if "```json" in response_text:
                json_str = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                json_str = response_text.split("```")[1].split("```")[0]

            data = json.loads(json_str.strip())

            action_type = ActionType[data.get("action", "UNKNOWN")]
            params = data.get("params", {})
            reasoning = data.get("reasoning", "")
            confidence = data.get("confidence", 0.0)

            return Action(
                action_type=action_type,
                params=params,
                reasoning=reasoning,
                confidence=confidence,
            )
        except (json.JSONDecodeError, KeyError, IndexError) as e:
            print(f"Failed to parse response: {e}")
            print(f"Raw response: {response_text[:500]}...")
            return None

    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyResult:
        """Analyze screenshot using Step-1V API."""
        if not self.is_available():
            return self._create_error_result(
                "Step-1V API key not configured",
                StrategyLevel.STEP1V
            )

        start_time = time.time()
        base64_image = self._encode_image(screenshot)
        system_prompt, user_prompt = self._build_prompt(goal, context)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                },
            ],
            "temperature": 0.2,
            "max_tokens": 512,
        }

        for attempt in range(self.max_retries):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()

                    result = response.json()
                    content = result["choices"][0]["message"]["content"]

                    action = self._parse_response(content)

                    if action:
                        self.record_success()
                        return StrategyResult(
                            success=True,
                            level=StrategyLevel.STEP1V,
                            action=action,
                            raw_response=content,
                            processing_time=time.time() - start_time,
                            metadata={
                                "model": self.model,
                                "attempt": attempt + 1,
                            }
                        )
                    else:
                        return self._create_error_result(
                            "Failed to parse API response",
                            StrategyLevel.STEP1V
                        )

            except httpx.TimeoutException:
                error_msg = f"API timeout (attempt {attempt + 1}/{self.max_retries})"
                if attempt < self.max_retries - 1:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                return self._create_error_result(error_msg, StrategyLevel.STEP1V)

            except httpx.HTTPStatusError as e:
                error_msg = f"API error: {e.response.status_code} - {e.response.text[:200]}"
                if e.response.status_code == 429:  # Rate limit
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (attempt + 1) * 2
                        time.sleep(wait_time)
                        continue
                return self._create_error_result(error_msg, StrategyLevel.STEP1V)

            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                return self._create_error_result(error_msg, StrategyLevel.STEP1V)

        return self._create_error_result(
            "All retry attempts exhausted",
            StrategyLevel.STEP1V
        )