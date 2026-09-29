// TypeScript looks for `<name>.d.mts` next to a relatively imported `.mjs` file
// (`import … from './synwevo-docs.mjs'`, the vendored-copy usage); the declarations live in
// synwevo-docs.d.ts, which the package's `exports` point at.
export * from './synwevo-docs.js';
