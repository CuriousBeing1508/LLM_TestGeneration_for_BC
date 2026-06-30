




import de.retest.recheck.auth.DefaultAuthenticationHandler;
import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;

@Test
public void test_loginPerformed() {
    DefaultAuthenticationHandler handler = 
                    new DefaultAuthenticationHandler();

    assertDoesNotThrow(() -> {
        handler.loginPerformed("dummyToken");
    });
}


Context passed
• Metadata, test formats, Goals… +
• Full focal method source code:

@Override
public void loginPerformed(final java.lang.String token) {
    de.retest.recheck.auth.DefaultAuthenticationHandler
            .logger.info("Login successful.");
}






