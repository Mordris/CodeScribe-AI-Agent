# Python Style Guide for Our Project

## Naming Conventions

- All variable and function names must use `snake_case`.
- All class names must use `PascalCase`.
- Constants must be `UPPER_SNAKE_CASE`.

## Error Handling

- Do not use bare `except:` clauses. Always specify the exception type, like `except ValueError:`.
- Use custom exception classes for application-specific errors.

## Docstrings

- Every public function, class, and module must have a Google-style docstring.
- Docstrings must clearly define arguments, return values, and any exceptions raised.
