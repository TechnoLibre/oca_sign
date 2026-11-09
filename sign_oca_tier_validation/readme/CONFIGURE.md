- On each tier definition (_Settings / Technical / Tier Validations / Tier Definition_),
  the _Sign Role_ field gives the role its reviewers sign as. It is created with the
  tier's name when a first document is sent; it can be set beforehand to reuse an
  existing role.
- A tier whose definition has _Comment_ checked requires the comment field of the
  document to be filled in to sign. The rule is the one the record's _Validate_ button
  applies (`has_comment`): a signature asks for a comment when any tier still to decide
  by the same reviewer asks for one, such as a later tier approved by bypass.
