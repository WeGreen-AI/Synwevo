/** 应用接入统一身份所需的全部浏览器端代码。
 *
 * `signInUrl` 是这里最要紧的一个：依赖方没有自己的口令框，唯一正确的去处是 `/auth/start`。
 * 把它放在共享包里，是为了让"口令只在一个地方输入"这条规则由一个函数保证，而不是由每个应用
 * 各自记得。
 */
export * from "./api";
export * from "./session";
export { platformHandoffUrl, isDefaultNext } from "./handoff";
export { SignedOut } from "./SignedOut";
export { PlatformNav } from "./PlatformNav";
export { NotificationBell, NotificationDrawer, useUnreadCount } from "./NotificationBell";
export { MessagesLink, useMessagesUnread, announceMessagesUnread } from "./MessagesLink";
