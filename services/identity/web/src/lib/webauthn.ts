/** Browser WebAuthn serialization shared by registration, sign-in and reauthentication. */
export type PublicKeyCredentialCreationOptionsJSON = Record<string, unknown>;

function b64urlToBuf(s: string): ArrayBuffer {
  const pad = s.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (s.length % 4)) % 4);
  const bin = atob(pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out.buffer;
}

function bufToB64url(b: ArrayBuffer): string {
  const bytes = new Uint8Array(b);
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** The pre-`parseCreationOptionsFromJSON` path: decode exactly the fields that must be buffers. */
export function manualCreationOptions(o: PublicKeyCredentialCreationOptionsJSON): PublicKeyCredentialCreationOptions {
  const src = o as {
    challenge: string;
    user: { id: string; name: string; displayName: string };
    excludeCredentials?: { id: string; type: string; transports?: string[] }[];
  };
  return {
    ...(o as unknown as PublicKeyCredentialCreationOptions),
    challenge: b64urlToBuf(src.challenge),
    user: { ...src.user, id: b64urlToBuf(src.user.id) },
    excludeCredentials: (src.excludeCredentials || []).map((c) => ({
      ...c, id: b64urlToBuf(c.id), type: "public-key" as const,
      transports: c.transports as AuthenticatorTransport[] | undefined,
    })),
  };
}

/** The shape the server verifies. Shared with the sign-in page. */
export function toJSON(cred: PublicKeyCredential): Record<string, unknown> {
  const r = cred.response as AuthenticatorAttestationResponse & AuthenticatorAssertionResponse;
  const out: Record<string, unknown> = {
    id: cred.id, rawId: bufToB64url(cred.rawId), type: cred.type,
    clientExtensionResults: cred.getClientExtensionResults(),
    response: { clientDataJSON: bufToB64url(r.clientDataJSON) } as Record<string, unknown>,
  };
  const resp = out.response as Record<string, unknown>;
  if (r.attestationObject) {
    resp.attestationObject = bufToB64url(r.attestationObject);
    if (typeof r.getTransports === "function") resp.transports = r.getTransports();
  }
  if (r.authenticatorData) resp.authenticatorData = bufToB64url(r.authenticatorData);
  if (r.signature) resp.signature = bufToB64url(r.signature);
  if (r.userHandle) resp.userHandle = bufToB64url(r.userHandle);
  return out;
}

export { b64urlToBuf, bufToB64url };
