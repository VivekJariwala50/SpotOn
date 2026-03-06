pipeline {
    agent any

    environment {
        APP_NAME = "smart-parking-app"
        CONTAINER_NAME = "smart-parking-dev"
        PUBLIC_URL = "http://13.58.211.204"
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Build Docker Image') {
            steps {
                sh """
                    docker build -t ${APP_NAME}:dev .
                """
            }
        }

        stage('Deploy to DEV') {
            steps {
                sh """
                    docker rm -f ${CONTAINER_NAME} || true
                    docker run -d --name ${CONTAINER_NAME} -p 80:5000 ${APP_NAME}:dev
                """
            }
        }

       stage('Trigger testRigor') {
  steps {
   withCredentials([string(credentialsId: 'testRigorToken', variable: 'testRigorToken')]) {
  sh '''
    set -e
    echo "Triggering testRigor..."
    curl -sS -X POST \
      -H "Content-Type: application/json" \
      -H "auth-token: $testRigorToken" \
      --data '{"forceCancelPreviousTesting":true}' \
      "https://api.testrigor.com/api/v1/apps/DYnF8LHyz83AeE7vv/retest"
    echo
  '''
}
  }
}
    
}

post {
    success {
        withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
            sh '''
                curl -X POST -H 'Content-type: application/json' \
                --data '{"text":"✅ Jenkins Build SUCCESS: Smart Parking app deployed successfully. DEV URL: http://13.58.211.204"}' \
                "$SLACK_WEBHOOK"
            '''
        }
    }

    failure {
        withCredentials([string(credentialsId: 'SLACK_WEBHOOK', variable: 'SLACK_WEBHOOK')]) {
            sh '''
                curl -X POST -H 'Content-type: application/json' \
                --data '{"text":"❌ Jenkins Build FAILED: Smart Parking pipeline encountered an error."}' \
                "$SLACK_WEBHOOK"
            '''
        }
    }

    always {
        echo "========================================"
        echo "Application is live at: http://13.58.211.204"
        echo "========================================"
    }
}
