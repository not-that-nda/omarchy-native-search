"""Subsequence and bounded typo matching with indexed candidate prefiltering."""
import re


def candidate_pattern(terms, tolerance):
    patterns = []
    for term in terms:
        patterns.append('.*'.join(re.escape(c) for c in term))
        # Any match within k edits retains one of k+1 disjoint chunks.
        edits = min(tolerance, max(0, (len(term) - 1) // 3))
        if edits:
            chunks = edits + 1
            patterns.extend(re.escape(term[i * len(term) // chunks:(i + 1) * len(term) // chunks])
                            for i in range(chunks))
    return '(' + '|'.join(patterns) + ')'


def distance(a, b, cap):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, char in enumerate(a, 1):
        row = [i]
        for j, other in enumerate(b, 1):
            row.append(min(row[-1] + 1, previous[j] + 1, previous[j - 1] + (char != other)))
        if min(row) > cap:
            return cap + 1
        previous = row
    return previous[-1]


def score(term, text, tolerance):
    term, text = term.casefold(), text.casefold()
    if term == text:
        return 0
    if term in text:
        return 5 if text.startswith(term) else 10
    position, first = -1, -1
    for char in term:
        position = text.find(char, position + 1)
        if position < 0:
            break
        if first < 0:
            first = position
    else:
        return 20 + min(30, position - first + 1 - len(term))
    edits = min(tolerance, max(0, (len(term) - 1) // 3))
    if edits:
        best = min((distance(term, word, edits) for word in re.split(r'[\W_]+', text) if word), default=edits + 1)
        if best <= edits:
            return 60 + best * 10
    return None


def rank(terms, basename, relative_path, tolerance):
    total = 0
    for term in terms:
        value = score(term, basename, tolerance)
        if value is None:
            value = score(term, relative_path, tolerance)
            if value is None:
                return None
            value += 100
        total += value
    return total
