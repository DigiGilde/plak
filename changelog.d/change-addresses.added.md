- A group admin can change a group's address, and a site admin with a role in
  the group a site's address. The old address redirects whoever may see the
  site through the 30th day, can be taken back until then, and is free for
  others after the nightly cleanup.
- A group admin can give a site an address that another site in the group
  recently gave up, which ends that site's redirect at once.
- The API for it: `PUT /groups/{group}/slug` and `PUT /sites/{group}/{site}/slug`,
  `previousSlugs` on groups and sites, and `slugRedirectDays` on `/me`.
