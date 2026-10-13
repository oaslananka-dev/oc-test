import { Plugin } from "@opencode/plugin"

/** F1 pinned-by-repository, local, read-only plugin fixture. No I/O, secrets or external dependencies. */
export default Plugin.define({
  id: "ocfilo-f1-proof",
  async setup(ctx) {
    await ctx.tool.transform((editor) => {
      editor.namespace({
        name: "ocfilo",
        description: "Restricted local ocfilo F1 verification tools",
      })
      editor.add({
        name: "proof",
        description: "Return a constant local proof that the F1 plugin is loaded",
        input: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        options: { namespace: "ocfilo", codemode: true },
        execute: async () => ({
          content: "ocfilo F1 read-only plugin proof: loaded on Ubuntu",
        }),
      })
    })
  },
})
