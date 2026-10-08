- Creating a group or site answers `SLUG_EXISTS` also for a slug that was
  recently the address of another group or site.
- The creation budget of 20 per hour counts changes of address as well.
- The audit log filters on group and site also find a change of address by
  its old slug, and a group's change by each of its sites.
- The API knows only the current address of a group or site: an old one
  answers 404, also to a workflow.
- A repository link that still took workflows without the site id requires
  it once its site or group gets another address.
