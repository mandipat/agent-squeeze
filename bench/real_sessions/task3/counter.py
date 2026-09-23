from collections import Counter
import re


def count_words(text):
    return len(re.findall(r'\b\w+\b', text.lower()))


def top_words(text, n):
    words = re.findall(r'\b\w+\b', text.lower())
    return Counter(words).most_common(n)
