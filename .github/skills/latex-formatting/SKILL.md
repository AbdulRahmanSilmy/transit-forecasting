---
name: latex-formatting
description: "Use whenever explaining mathematics or presenting equations in responses for this repository, especially when fixing math that does not render. Keep LaTeX notation and use dollar-sign math delimiters."
---

# LaTeX Formatting

## Response Rules

- Keep mathematical equations in LaTeX unless the user explicitly requests
  plain text. A rendering problem is not a request to abandon LaTeX.
- Use `$$ ... $$` for display equations, with each delimiter on its own line
  and blank lines separating the equation from surrounding prose.
- Use `$ ... $` for inline mathematics.
- Do not use `\[ ... \]` or `\( ... \)` delimiters in responses.
- Do not wrap equations in code fences or backticks when they should render.
- Write ordinary LaTeX commands with single backslashes in response text.
  Double backslashes only when required by a storage format such as JSON.
- Define symbols and units in nearby prose. Keep notation consistent with
  the code and notebook being explained.

## Display Equation Example

Use this Markdown structure in a response, without the example's code fence:

```markdown
Delay grows when elapsed time exceeds the scheduled allowance:

$$
D_c - D_u = T + W - S
$$

Here, all durations and delays are in seconds.
```

## Rendering Corrections

If the user reports that math did not render, resend the relevant explanation
using dollar-sign delimiters while preserving the mathematics. Do not claim
that rendering has been verified unless it has actually been observed.
