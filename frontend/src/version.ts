/** The release this build ships as; `dev` outside a release build. */
export function appVersion(): string {
  return __PLAK_VERSION__;
}
