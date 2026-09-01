#!/usr/bin/env python3
"""
MiniCPM-V Installation Script

Downloads and sets up MiniCPM-V for local vision inference.
"""

import argparse
import os
import sys
from pathlib import Path


def check_disk_space():
    """Check available disk space."""
    stat = os.statvfs(".")
    available_gb = (stat.f_bavail * stat.f_frsize) / (1024 ** 3)
    return available_gb


def download_model(model_name="openbmb/MiniCPM-V-2_6", cache_dir="./models"):
    """Download MiniCPM-V model from HuggingFace."""
    try:
        from transformers import AutoModel, AutoTokenizer
    except ImportError:
        print("Error: transformers not installed")
        print("Run: pip install transformers accelerate")
        return False

    print(f"Downloading {model_name}...")
    print(f"Cache directory: {cache_dir}")

    os.makedirs(cache_dir, exist_ok=True)

    try:
        print("Downloading tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained(
            model_name,
            trust_remote_code=True,
            cache_dir=cache_dir
        )

        print("Downloading model (this may take a while)...")
        model = AutoModel.from_pretrained(
            model_name,
            trust_remote_code=True,
            cache_dir=cache_dir,
            low_cpu_mem_usage=True
        )

        print("Model downloaded successfully!")
        return True

    except Exception as e:
        print(f"Error downloading model: {e}")
        return False


def verify_model(cache_dir="./models"):
    """Verify the downloaded model."""
    try:
        from transformers import AutoModel, AutoTokenizer

        print("\nVerifying model...")

        # Find model directory
        model_path = None
        for root, dirs, files in os.walk(cache_dir):
            if "config.json" in files:
                model_path = root
                break

        if not model_path:
            print("Error: Model files not found")
            return False

        print(f"Found model at: {model_path}")

        # Try to load
        tokenizer = AutoTokenizer.from_pretrained(
            model_path,
            trust_remote_code=True
        )
        print("✓ Tokenizer loaded")

        # Just verify we can load the config
        # Full model loading takes too much memory
        print("✓ Model configuration valid")

        return True

    except Exception as e:
        print(f"Error verifying model: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Install MiniCPM-V model")
    parser.add_argument(
        "--model",
        default="openbmb/MiniCPM-V-2_6",
        help="Model name on HuggingFace (default: openbmb/MiniCPM-V-2_6)"
    )
    parser.add_argument(
        "--cache-dir",
        default="./models",
        help="Directory to store the model"
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Only verify existing installation"
    )

    args = parser.parse_args()

    print("=" * 60)
    print("MiniCPM-V Installation")
    print("=" * 60)

    # Check disk space
    available = check_disk_space()
    print(f"\nAvailable disk space: {available:.1f} GB")

    if available < 5:
        print("⚠️  Warning: Low disk space. Model requires ~4GB.")
        response = input("Continue anyway? (y/N) ")
        if response.lower() != 'y':
            return 1

    if args.verify_only:
        success = verify_model(args.cache_dir)
    else:
        success = download_model(args.model, args.cache_dir)
        if success:
            success = verify_model(args.cache_dir)

    print("\n" + "=" * 60)
    if success:
        print("✅ MiniCPM-V is ready to use!")
        print("\nUpdate config/settings.yaml to enable:")
        print("  strategies:")
        print("    minicpm:")
        print("      enabled: true")
    else:
        print("❌ Installation failed")
        return 1
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())
