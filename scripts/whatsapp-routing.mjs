import { createHash } from "node:crypto";

export function whatsappRouting(env) {
  const phone = (value, required = false) => {
    const number = String(value || "").replace(/[+ ()-]/g, "");
    if ((!number && required) || (number && !/^[1-9][0-9]{7,14}$/.test(number)))
      throw new Error(
        "Use an international WhatsApp number with country code.",
      );
    return number;
  };
  const owner = phone(env.WHATSAPP_OWNER || "");
  const business = phone(env.WHATSAPP_BUSINESS_CONTACT || "");
  const group = String(env.WHATSAPP_GROUP || "").trim();
  if (group && !/^[0-9]{5,30}(?:-[0-9]{5,20})?@g\.us$/.test(group))
    throw new Error("Use a WhatsApp group ID ending in @g.us.");
  const team = JSON.parse(env.WHATSAPP_TEAM_CONTACTS || "[]");
  if (!Array.isArray(team) || team.length > 20)
    throw new Error("Add up to 20 team contacts.");
  const contacts = owner
    ? [{
        number: owner,
        name: "Owner",
        role: "owner",
      }]
    : [];
  if (business)
    contacts.push({
      number: business,
      name: "Business contact",
      role: "business",
    });
  for (const item of team) {
    if (
      !item ||
      Object.keys(item).some((k) => !["name", "number", "calls", "role"].includes(k)) ||
      typeof item.name !== "string" ||
      !item.name.trim() ||
      item.name.length > 80 ||
      /[\r\n\0]/.test(item.name)
    )
      throw new Error("Each team contact needs a name and number.");
    contacts.push({
      number: phone(item.number, true),
      name: item.name.trim(),
      role: typeof item.role === "string" && item.role.trim() ? item.role.trim() : "team",
      calls: Boolean(item.calls),
    });
  }
  if (new Set(contacts.map((c) => c.number)).size !== contacts.length)
    throw new Error("Each WhatsApp number can be assigned only once.");
  const bindings = contacts.map((c) => {
    const profile = c.role === "owner" ? "leo" : "team-phone-" + c.number;
    return {
      ...c,
      route: c.role === "owner" ? "owner" : "contact-" + c.number,
      profile,
      text_profile: profile + "-text",
      calls: c.calls !== undefined ? Boolean(c.calls) : c.role !== "team",
    };
  });
  return {
    owner,
    business,
    group,
    contacts: bindings,
    group_profile: group
      ? "team-phone-group-" +
        createHash("sha256").update(group).digest("hex").slice(0, 16)
      : "team-whatsapp-social",
  };
}
