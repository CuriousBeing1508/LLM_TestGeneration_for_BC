



// Client: DefaultFeatureSupport.setup(Bootstrap)
ObjectMapper om = bootstrap.getObjectMapper();
// OK: ObjectMapper constructed successfully
om.addMixIn(SessionStore.class,
InstantiateByClassNameMixin.class);
// ... mix-ins registered





org.json.JSONObject.getString(key)

..... Other details ...
Client's Focal method: SecurityCommands.getAclServicesRules()

// Dependency methods invoked by focal method: 
        - org.json.JSONObject.getString(key)
        - org.json.JSONObject.keys()

..... Other details ...



import org.json.JSONObject;
import org.junit.Test;
import java.util.Iterator;
import static org.junit.Assert.*;

public class BBC115U15Test {
  @Test public void test_getAclServicesRules() throws Exception {
    String json = "{\"wfs\":\"admin\",\"wms\":\"user\"}";  
    JSONObject o = new JSONObject(json);
    StringBuilder b = new StringBuilder();
    for (Iterator<String> it = o.keys(); it.hasNext(); ) {
      String k = it.next();
      b.append(k).append(" = ").append(o.getString(k));  
    }
       
  }



