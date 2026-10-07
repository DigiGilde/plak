- The admin pages load with fewer database round trips, and an admin request
  no longer writes to the database. A member's last login is now the moment
  they signed in, not their latest request.
