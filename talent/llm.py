"""Every model call goes through here. Any OpenAI-compatible endpoint works: OpenRouter, Azure, OpenAI."""
import os

from openai import OpenAI

client = OpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
MODEL = os.environ["LLM_MODEL"]


def ask(prompt, schema, model=MODEL):
    """Send one prompt and get the reply back as an instance of `schema`, a pydantic model."""
    reply = client.chat.completions.parse(
        model=model, messages=[{"role": "user", "content": prompt}], response_format=schema)
    return reply.choices[0].message.parsed


def ask_with_search(prompt, schema, model=MODEL):
    """Like `ask`, but the model can search the web first. Needs an endpoint with the web_search tool."""
    reply = client.responses.parse(
        model=model, input=prompt, tools=[{"type": "web_search"}], text_format=schema)
    return reply.output_parsed
