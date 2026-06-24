import re


def format_description(text: str) -> str:
    """Add a blank line after each sentence so the description has clear paragraph breaks."""
    if not text:
        return ""
    # Add \n\n after sentence-ending punctuation followed by a space or end of string
    text = re.sub(r'([.!?])\s+', r'\1\n\n', text.strip())
    # Collapse any runs of 3+ newlines down to 2
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text
