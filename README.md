# zd-design-system plugin

The four Claude skills that build and maintain a ZeroDegrees client design system. They're published as one plugin to the organization's plugin library on claude.ai, so a fix released here reaches every client project at once.

**How it all fits together:** the [Design System Pipeline doc](https://claude.ai/artifact/MW1v9ZqHbwHb33ebUZG48Z) walks through every stage, from setting up a client to everyday changes, and what you approve at each one.

| Skill | What it does |
| --- | --- |
| `brand-intake` | Audits a client's brand and proposes tokens for it |
| `port-tokens` | Writes an approved proposal into Figma |
| `ds-sync` | Keeps Figma and the markdown in agreement |
| `build-component` | Adds a component: spec first, then Figma |

The design system itself (tokens, foundations, components, `FORMATS.md`) isn't here. It lives in each client repo, made from [zd-design-system-starter](https://github.com/ZeroDegreesInc/zd-design-system-starter).

## Installing

Install **zd-design-system** once from the organization's plugin library on claude.ai. The skills then work in any client repo you open in Claude.

## Layout

```
.claude-plugin/plugin.json   name and version
skills/<skill>/SKILL.md      what Claude follows
skills/<skill>/scripts/      the scripts each skill runs
```

The four skills ship together on purpose: `port-tokens` and `brand-intake` use `ds-sync`'s scripts by finding them in the folder next to theirs.

## Format versions

Each client repo's `design-system.config.yaml` names the file format it's written in (`format_version`; a repo without one is format 1). `synclib.FORMAT_VERSIONS` lists the formats these scripts read, and every script stops with a plain message when a project's format isn't one of them, rather than half-writing it.

When a release changes a file format in a way older scripts would misread:

1. Raise the format in the starter's `FORMATS.md` and config.
2. Add the new number to `FORMAT_VERSIONS`. Keep the old one while the scripts can still read it.
3. Release the plugin before any repo moves to the new format.

## Releasing an update

1. **Change the skills on a branch** and open a pull request.
2. **Test on the test client** ([zd-sagora-test](https://github.com/ZeroDegreesInc/zd-sagora-test)). Run each changed script from this working copy against it, for example:
   ```bash
   python3 -I skills/ds-sync/scripts/check.py --root ~/Desktop/zd-sagora-test/design-system --no-figma
   ```
   For changes to a `SKILL.md`, run the skill there end to end.
3. **Raise the version** in `.claude-plugin/plugin.json` (patch for fixes, minor for new steps, major when a format changes) and merge.
4. **Tag it** with the version (`v0.2.0`) and move the major tag (`v0`, or `v1` from 1.0) to the same commit. Client repos' pull request checks run `check.py` from the major tag.
5. **Publish** the new version to the organization's library from Claude ("publish the zd-design-system plugin"). An admin may need to approve it.
