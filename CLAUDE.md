# Project Guidelines

## Commit Rules

- Commit messages must be in English

## Skill Naming Convention

- The skill directory name, SKILL.md `name` field, and plugin name must be identical
- Use lowercase with hyphens (kebab-case), e.g., `my-skill-name`
- When renaming a skill, update all three locations:
  1. Directory name
  2. SKILL.md `name` field
  3. `.claude-plugin/marketplace.json` plugin `name` and `skills` path

## README Updates

- When adding a new skill, update `README.md`
- Add the new skill to the "Available Skills" table

## Skill Description Rules

The `description` field in SKILL.md must include two parts:
1. **What it does**: Brief description of the skill's functionality
2. **When to use**: Tell the agent when to trigger this skill

Example format:
```yaml
description: Convert Mermaid diagrams to images. Use when user wants to export/save/convert Mermaid code to PNG/JPG/SVG/PDF format.
```

## Adding a New Skill

1. Create a directory: `./your-skill-name/`
2. Add `SKILL.md` with proper frontmatter (`name`, `description`, `version`)
3. Add the plugin entry to `.claude-plugin/marketplace.json`
4. Update `README.md`
