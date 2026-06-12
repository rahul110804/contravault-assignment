"""LLM client abstraction for Groq via OpenAI SDK."""
import json
import logging
import time
from typing import Any

from openai import OpenAI

from config import GROQ_API_KEY, GROQ_MODEL, GROQ_BASE_URL

logger = logging.getLogger(__name__)


class GroqClient:
    """Wrapper around Groq API with retry logic and structured output."""
    
    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or GROQ_API_KEY
        self.model = model or GROQ_MODEL
        
        if not self.api_key:
            raise ValueError(
                "Groq API key not provided. Set GROQ_API_KEY env var "
                "or pass api_key parameter."
            )
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=GROQ_BASE_URL
        )
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
    
    def generate(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_retries: int = 3,
        retry_delay: float = 2.0
    ) -> str:
        """Generate text from a prompt.
        
        Args:
            prompt: The user prompt (TEXT ONLY)
            system_instruction: Optional system instruction
            temperature: Sampling temperature (low for deterministic)
            max_retries: Number of retries on failure
            retry_delay: Base delay between retries (exponential backoff)
        
        Returns:
            Generated text response, or an error message if all retries fail.
        """
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})
        
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature
                )
                
                self.total_calls += 1
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens or 0
                    self.total_output_tokens += response.usage.completion_tokens or 0
                
                return response.choices[0].message.content or ""
            
            except Exception as e:
                # Handle rate limits
                wait = retry_delay * (2 ** attempt)
                if hasattr(e, 'response') and e.response is not None:
                    retry_after = e.response.headers.get('retry-after')
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            pass
                
                logger.warning(f"Groq API call failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {wait}s")
                if attempt < max_retries - 1:
                    time.sleep(wait)
                else:
                    return f"ERROR: LLM generation failed after {max_retries} attempts. Reason: {e}"
        
        return "ERROR: LLM generation failed."
    
    def generate_json(
        self,
        prompt: str,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_retries: int = 3
    ) -> dict | list:
        """Generate a JSON response from a prompt.
        
        Instructs the model to return valid JSON and parses it.
        """
        json_instruction = (
            (system_instruction or "") +
            "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown fences, no explanation text. "
            "Just raw JSON that can be parsed by json.loads()."
        )
        
        messages = [
            {"role": "system", "content": json_instruction},
            {"role": "user", "content": prompt}
        ]
        
        for attempt in range(max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature
                )
                
                self.total_calls += 1
                if response.usage:
                    self.total_input_tokens += response.usage.prompt_tokens or 0
                    self.total_output_tokens += response.usage.completion_tokens or 0
                
                text = response.choices[0].message.content or "{}"
                # Strip markdown fences if present despite instructions
                text = text.strip()
                if text.startswith("```"):
                    text = text.split("\n", 1)[1] if "\n" in text else text[3:]
                    if text.endswith("```"):
                        text = text[:-3]
                    text = text.strip()
                
                return json.loads(text)
            
            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse failed (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    # Return error structure so scoring layer records failure without crashing
                    return {"error": "json_parse_error", "message": str(e)}
            except Exception as e:
                wait = 2.0 * (2 ** attempt)
                if hasattr(e, 'response') and e.response is not None:
                    retry_after = e.response.headers.get('retry-after')
                    if retry_after:
                        try:
                            wait = float(retry_after)
                        except ValueError:
                            pass
                
                logger.warning(f"Groq API call failed (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(wait)
                else:
                    return {"error": "rate_limit_exceeded", "message": str(e)}
        
        return {"error": "generation_failed"}
    
    @property
    def usage_summary(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens
        }
