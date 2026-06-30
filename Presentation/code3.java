import de.retest.recheck.auth.DefaultAuthenticationHandler;
import static org.junit.jupiter.api.Assertions.assertTrue;

@Test
public void test_loginPerformed() {
    DefaultAuthenticationHandler handler = 
                new DefaultAuthenticationHandler();
    ByteArrayOutputStream outContent = 
                new ByteArrayOutputStream();
    System.setOut(new PrintStream(outContent));

    handler.loginPerformed("dummyToken");

    assertTrue(outContent.toString().contains("Login successful."));
}


Context passed
• Metadata, test formats, Goals… +
• Full focal class source code:
public class DefaultAuthenticationHandler {

    private static final Logger logger =
        LoggerFactory.getLogger(...);

    @Override
    public void loginPerformed(final java.lang.String token) {
        de.retest.recheck.auth.DefaultAuthenticationHandler
            .logger.info("Login successful.");
}
    // .... Other methods....
}



SLF4J: No SLF4J providers were found.
SLF4J: Defaulting to no-operation (NOP) logger implementation
SLF4J: Ignoring binding found at
[...logback-classic/1.2.11...StaticLoggerBinder.class]
