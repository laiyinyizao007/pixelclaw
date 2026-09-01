"""
MiniCPM-V Strategy

Uses the local MiniCPM-V vision-language model for screen understanding.
Level 2 in the fallback hierarchy - works without internet connection.

Note: Requires transformers, torch, and related dependencies to be installed.
"""

import json
import time
from typing import Any, Dict, Optional

from PIL import Image

from .base import Action, ActionType, BaseStrategy, StrategyLevel, StrategyResult


class MiniCPMStrategy(BaseStrategy):
    """
    Strategy using local MiniCPM-V vision-language model.

    This is a lightweight VLM that can run on Raspberry Pi 5 with sufficient RAM.
    Model: MiniCPM-V-2_6 (or similar)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.model_path = self.config.get("model_path", "./models/MiniCPM-V-2_6")
        self.device = self.config.get("device", "cpu")
        self.quantization = self.config.get("quantization", "int8")
        self.max_new_tokens = self.config.get("max_new_tokens", 512)
        self.temperature = self.config.get("temperature", 0.7)

        self._model = None
        self._tokenizer = None
        self._loaded = False

    def get_level(self) -> StrategyLevel:
        return StrategyLevel.MINICPM

    def is_available(self) -> bool:
        """Check if the model is available and can be loaded."""
        if self._loaded:
            return True

        try:
            # Try to import required libraries
            import torch
            from transformers import AutoModel, AutoTokenizer
            return True
        except ImportError:
            return False

    def _load_model(self) -> bool:
        """Lazy load the model on first use."""
        if self._loaded:
            return True

        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            # Check for MPS (Apple Silicon) or CUDA
            if self.device == "auto":
                if torch.backends.mps.is_available():
                    self.device = "mps"
                elif torch.cuda.is_available():
                    self.device = "cuda"
                else:
                    self.device = "cpu"

            self._tokenizer = AutoTokenizer.from_pretrained(
                self.model_path,
                trust_remote_code=True
            )

            # Load model with appropriate settings for the device
            load_kwargs = {
                "trust_remote_code": True,
                "low_cpu_mem_usage": True,
            }

            if self.device == "cpu":
                # Use int8 quantization on CPU for Raspberry Pi
                load_kwargs["torch_dtype"] = torch.float32
            else:
                load_kwargs["torch_dtype"] = torch.float16

            self._model = AutoModel.from_pretrained(
                self.model_path,
                **load_kwargs
            )
            self._model = self._model.to(self.device).eval()
            self._loaded = True

            return True

        except Exception as e:
            print(f"Failed to load MiniCPM model: {e}")
            return False

    def _build_prompt(self, goal: str, context: Optional[Dict[str, Any]] = None) -> str:
        """Build the prompt for MiniCPM-V."""
        prompt = """You are controlling an Android device. Analyze the screenshot and determine the next action.

Available actions:
- TAP(x, y): Tap at coordinates
- SWIPE(x1, y1, x2, y2): Swipe from start to end
- LONG_PRESS(x, y, duration): Long press
- TYPE(text): Type text
- BACK(): Press back button
- HOME(): Press home button
- WAIT(seconds): Wait
- COMPLETE(): Task completed

Respond with ONLY this JSON format:
{"action": "TAP", "params": {"x": 500, "y": 1000}, "reasoning": "Click the button", "confidence": 0.9}"""

        if context:
            history = context.get("action_history", [])
            if history:
                prompt += f"\n\nPrevious: {', '.join(history[-3:])}"

        prompt += f"\n\nGoal: {goal}"
        return prompt

    def _parse_response(self, response_text: str) -> Optional[Action]:
        """Parse the model response into an Action."""
        try:
            # Try to extract JSON from the response
            text = response_text.strip()

            # Find JSON object in the response
            start_idx = text.find("{")
            end_idx = text.rfind("}")

            if start_idx != -1 and end_idx != -1:
                json_str = text[start_idx:end_idx + 1]
                data = json.loads(json_str)

                return Action(
                    action_type=ActionType[data.get("action", "UNKNOWN")],
                    params=data.get("params", {}),
                    reasoning=data.get("reasoning", ""),
                    confidence=data.get("confidence", 0.0),
                )
            return None

        except (json.JSONDecodeError, KeyError) as e:
            print(f"Failed to parse MiniCPM response: {e}")
            return None

    async def analyze(
        self,
        screenshot: Image.Image,
        goal: str,
        context: Optional[Dict[str, Any]] = None
    ) -> StrategyResult:
        """Analyze screenshot using local MiniCPM-V model."""
        if not self.is_available():
            return self._create_error_result(
                "MiniCPM dependencies not installed (transformers, torch)",
                StrategyLevel.MINICPM
            )

        if not self._load_model():
            return self._create_error_result(
                "Failed to load MiniCPM model",
                StrategyLevel.MINICPM
            )

        start_time = time.time()
        prompt = self._build_prompt(goal, context)

        try:
            # Prepare the image and text for the model
            msgs = [{'role': 'user', 'content': [screenshot, prompt]}]

            # Generate response
            res = self._model.chat(
                image=None,  # Already in msgs
                msgs=msgs,
                tokenizer=self._tokenizer,
                sampling=True,
                temperature=self.temperature,
                max_new_tokens=self.max_new_tokens,
            )

            action = self._parse_response(res)

            if action:
                self.record_success()
                return StrategyResult(
                    success=True,
                    level=StrategyLevel.MINICPM,
                    action=action,
                    raw_response=res,
                    processing_time=time.time() - start_time,
                    metadata={
                        "model": "MiniCPM-V",
                        "device": self.device,
                    }
                )
            else:
                return self._create_error_result(
                    "Failed to parse model response",
                    StrategyLevel.MINICPM
                )

        except Exception as e:
            return self._create_error_result(
                f"Model inference error: {str(e)}",
                StrategyLevel.MINICPM
            )

    def unload(self):
        """Unload the model to free memory."""
        if self._model:
            import torch
            del self._model
            del self._tokenizer
            torch.cuda.empty_cache() if torch.cuda.is_available() else None
            self._model = None
            self._tokenizer = None
            self._loaded = False