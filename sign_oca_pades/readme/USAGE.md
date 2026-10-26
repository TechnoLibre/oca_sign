1. Configure a sealing certificate (see _Configuration_).
2. Send a sign request as usual. The document receives its form fields and the pages
   reserved for the certificate. Each time a signer signs, the signer's fields are
   filled and a new seal signs the signer's signature field.
3. When the last signer signs, the certificate fills the reserved pages and the final
   seal is added. The signed copy mailed to the signers is the sealed one.
4. On the request, the _Seals_ section shows the seal status, the number of seals, the
   original and final SHA-256 and the result of each seal check. _Verify Seals_ checks
   the stored document again.

The seals can also be checked in any PDF reader that supports signatures: each one is
shown as valid as long as the bytes it covers are unchanged. Whether the certificate is
trusted depends on the reader and on the certificate authority that issued it.
