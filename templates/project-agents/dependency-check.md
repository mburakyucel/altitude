name: Dependency check
days: Mon Tue Wed Thu Fri
time: 07:00
tell: found
summary: Looks for security fixes and major releases of what the project uses

Find every dependency manifest in the repository (for example pyproject.toml, requirements files,
package.json, Cargo.toml, go.mod) and check each direct dependency for published security fixes and
major releases newer than the version the project uses. Use the project's own audit command if it
documents one. Report which packages need an update, how urgent each is, and what in the project the
change would touch. If every dependency is current, say so in one line.
