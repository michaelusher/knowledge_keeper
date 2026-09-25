"""Generate the demo corpus (see knowledge_keeper/sample_corpus.py for what's planted).

Usage: python scripts/make_sample_corpus.py [output_dir]
The app's Documents page has a "Load demo documents" button that does the same thing.
"""
import sys

from knowledge_keeper.sample_corpus import generate

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "sample_corpus"
    files = generate(out)
    print(f"Sample corpus written to {files[0].parent.resolve()}")
