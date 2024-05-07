import json
import math
import pathlib
import random
import subprocess
import time
from typing import Any, Dict, Tuple
import dotenv
import os
from colorama import Fore, Style
from transformers import LlamaTokenizerFast


RESULTS_VERSION = "2023-08-31"


def build_providers(base_url: str | None) -> dict:
    return {
        "openai": {
            "environment": {"set": {"OPENAI_API_BASE": "https://api.openai.com/v1"}}
        },
        "anthropic": {},
        "cohere": {},
        "vertex_ai": {},
        "huggingface": {"environment": {"set": {"HUGGINGFACE_API_BASE": base_url}}},
        "anyscale": {
            "environment": {
                "set": {"OPENAI_API_BASE": "https://api.endpoints.anyscale.com/v1"},
                "map": {"OPENAI_API_KEY": "ANYSCALE_API_KEY"},
            }
        },
        "replicate": {},
        "mistral": {},
        "fireworks": {
            "environment": {
                "set": {"OPENAI_API_BASE": "https://api.fireworks.ai/inference/v1"},
                "map": {"OPENAI_API_KEY": "FIREWORKS_API_KEY"},
            }
        },
        "deepinfra": {},
        "bedrock": {},
        "perplexity": {
            "environment": {
                "set": {"OPENAI_API_BASE": "https://api.perplexity.ai"},
                "map": {"OPENAI_API_KEY": "PERPLEXITY_API_KEY"},
            }
        },
        "groq": {},
        "lepton": {
            "environment": {
                "set": {"OPENAI_API_BASE": base_url},
                "map": {"OPENAI_API_KEY": "LEPTON_API_KEY"},
            }
        },
        "octo-ai": {
            "environment": {
                "set": {"OPENAI_API_BASE": "https://text.octoai.run/v1"},
                "map": {"OPENAI_API_KEY": "OCTO_AI_API_KEY"},
            }
        },
        "together_ai": {},
        "azure": {
            "environment": {
                "set": {"OPENAI_API_BASE": base_url},
                "map": {"OPENAI_API_KEY": "AZURE_API_KEY"},
            }
        },
        "azure-openai": {
            "environment": {
                "set": {
                    "AZURE_API_BASE": base_url,
                    "AZURE_API_VERSION": "2024-02-15-preview",
                },
                "map": {
                    "AZURE_API_KEY": (
                        "AZURE_CANADA_EAST_OPENAI_KEY"
                        if base_url and "canada-east" in base_url
                        else (
                            "AZURE_NORTH_CENTRAL_US_OPENAI_KEY"
                            if base_url and "north-central" in base_url
                            else "AZURE_EAST_US_2_OPENAI_KEY"
                        )
                    ),
                },
            }
        },
        "cloudflare-workers": {},
    }


class LLMPerfResults:
    def __init__(
        self,
        name: str,
        metadata: Dict[str, Any] = None,
    ):
        self.name = name
        self.metadata = metadata or {}
        self.timestamp = int(time.time())
        self.metadata["timestamp"] = self.timestamp
        self.version = RESULTS_VERSION

    def to_dict(self):
        data = {
            "version": self.version,
            "name": self.name,
        }
        data.update(self.metadata)
        data = flatten_dict(data)
        return data

    def json(self):
        data = self.to_dict()
        return json.dumps(data)


def upload_to_s3(results_path: str, s3_path: str) -> None:
    """Upload the results to s3.

    Args:
        results_path: The path to the results file.
        s3_path: The s3 path to upload the results to.

    """

    command = ["aws", "s3", "sync", results_path, f"{s3_path}/"]
    result = subprocess.run(command)
    if result.returncode == 0:
        print("Files uploaded successfully!")
    else:
        print("An error occurred:")
        print(result.stderr)


def randomly_sample_sonnet_lines_prompt(
    prompt_tokens_mean: int = 550,
    prompt_tokens_stddev: int = 250,
    expect_output_tokens: int = 150,
) -> Tuple[str, int]:
    """Generate a prompt that randomly samples lines from a the shakespeare sonnet at sonnet.txt.

    Args:
        prompt_length_mean: The mean length of the prompt to generate.
        prompt_len_stddev: The standard deviation of the length of the prompt to generate.
        expect_output_tokens: The number of tokens to expect in the output. This is used to
        determine the length of the prompt. The prompt will be generated such that the output
        will be approximately this many tokens.

    Note:
        tokens will be counted from the sonnet using the Llama tokenizer. Using one tokenizer
        ensures a fairer comparison across different LLMs. For example, if gpt 3.5 tokenizes
        a prompt in less tokens than Llama2, then this will be reflected in the results since
        they will be fed identical prompts.

    Returns:
        A tuple of the prompt and the length of the prompt.
    """

    tokenizer = LlamaTokenizerFast.from_pretrained(
        "hf-internal-testing/llama-tokenizer"
    )

    get_token_length = lambda text: len(tokenizer.encode(text))

    prompt = (
        "Randomly stream lines from the following text "
        f"with {expect_output_tokens} output tokens. "
        "Don't generate eos tokens:\n\n"
    )
    # get a prompt length that is at least as long as the base
    num_prompt_tokens = sample_random_positive_int(
        prompt_tokens_mean, prompt_tokens_stddev
    )
    while num_prompt_tokens < get_token_length(prompt):
        num_prompt_tokens = sample_random_positive_int(
            prompt_tokens_mean, prompt_tokens_stddev
        )
    remaining_prompt_tokens = num_prompt_tokens - get_token_length(prompt)
    sonnet_path = pathlib.Path(__file__).parent.resolve() / "sonnet.txt"
    with open(sonnet_path, "r") as f:
        sonnet_lines = f.readlines()
    random.shuffle(sonnet_lines)
    sampling_lines = True
    while sampling_lines:
        for line in sonnet_lines:
            line_to_add = line
            if remaining_prompt_tokens - get_token_length(line_to_add) < 0:
                # This will cut off a line in the middle of a word, but that's ok since an
                # llm should be able to handle that.
                line_to_add = line_to_add[: int(math.ceil(remaining_prompt_tokens))]
                sampling_lines = False
                prompt += line_to_add
                break
            prompt += line_to_add
            remaining_prompt_tokens -= get_token_length(line_to_add)
    return (prompt, num_prompt_tokens)


def sample_random_positive_int(mean: int, stddev: int) -> int:
    """Sample random numbers from a gaussian distribution until a positive number is sampled.

    Args:
        mean: The mean of the gaussian distribution to sample from.
        stddev: The standard deviation of the gaussian distribution to sample from.

    Returns:
        A random positive integer sampled from the gaussian distribution.
    """
    ret = -1
    while ret <= 0:
        ret = int(random.gauss(mean, stddev))
    return ret


def flatten_dict(d, parent_key="", sep="_"):
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def setup_environment_variables(environment: dict) -> None:
    """
    Load environment variables from a .env file and set or map new variables based on the provided dictionary.

    Args:
        environment (dict): A dictionary containing 'set' and/or 'map' keys with sub-dictionaries as values.
            'set' is used to directly set environment variables.
            'map' is used to map existing environment variables to new keys.
    """
    # Load environment variables from a .env file
    dotenv.load_dotenv()

    # Set new environment variables if 'set' key is present
    if "set" in environment:
        for key, value in environment["set"].items():
            os.environ[key] = value
            # print(
            #     "Setting environment variable "
            #     + Fore.GREEN
            #     + f"{key}"
            #     + Style.RESET_ALL
            #     + " to "
            #     + Fore.GREEN
            #     + f"{value}"
            #     + Style.RESET_ALL
            # )

    # Map existing environment variables to new keys if 'map' key is present
    if "map" in environment:
        for key, value in environment["map"].items():
            mapped_value = os.getenv(value)
            os.environ[key] = mapped_value
            # print(
            #     "Mapping environment variable "
            #     + Fore.YELLOW
            #     + f"{key}"
            #     + Style.RESET_ALL
            #     + " (from "
            #     + Fore.YELLOW
            #     + f"{value}"
            #     + Style.RESET_ALL
            #     + ")"
            # )
